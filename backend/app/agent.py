from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path, PurePosixPath
from typing import Any, cast

from deepagents import create_deep_agent
from deepagents.middleware.async_subagents import AsyncSubAgent
from deepagents.middleware.memory import MemoryMiddleware
from deepagents.middleware.subagents import CompiledSubAgent, SubAgent

from app.catalog import build_model, resolve_agent
from app.runtime.extensions import (
    SandboxConfig,
    resolve_backend,
)
from app.runtime.sse_bridge import normalize_runtime_event
from app.settings import Settings

__all__ = (
    "DeepAgentsRunContext",
    "build_deep_agent",
    "normalize_runtime_event",
)


@dataclass(frozen=True)
class DeepAgentsRunContext:
    session_id: str
    run_id: str
    username: str
    thread_id: str
    session_metadata: dict[str, Any]
    current_attachments: tuple[dict[str, Any], ...] = ()
    session_attachments: tuple[dict[str, Any], ...] = ()

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def items(self) -> list[tuple[str, Any]]:
        return [(field.name, getattr(self, field.name)) for field in fields(self)]


def build_deep_agent(settings: Settings, model_id: str | None = None) -> Any:
    settings.assert_runtime_persistence_allowed()
    agent = resolve_agent(settings)
    selected_model_id = model_id or agent.get("model")
    middleware = list(agent["middleware"])
    sandbox_settings = settings.sandbox_settings()
    sandbox_settings["skills_root_dir"] = agent.get("skills_path")
    sandbox_settings["memory_root_dir"] = agent.get("memory_path")
    sandbox_settings["mounts"] = _subagent_resource_mounts(tuple(agent["subagents"]))
    sandbox_config = SandboxConfig.from_mapping(sandbox_settings)
    backend = resolve_backend(sandbox_config)
    permissions = list(agent["permissions"])
    return create_deep_agent(
        model=build_model(settings, str(selected_model_id) if selected_model_id else None),
        tools=list(agent["tools"]),
        middleware=middleware,
        skills=_mounted_resource_paths(agent["skills"], agent.get("skills_path"), "/skills"),
        memory=_mounted_resource_paths(agent["memory"], agent.get("memory_path"), "/memory"),
        permissions=permissions,
        subagents=_deepagents_subagents(tuple(agent["subagents"]), backend),
        system_prompt=agent["system_prompt"],
        context_schema=DeepAgentsRunContext,
        checkpointer=settings.runtime_checkpointer(),
        store=settings.runtime_store(),
        backend=backend,
        interrupt_on=cast(Any, settings.deepagents_interrupt_on or None),
        debug=settings.deepagents_debug,
        name=settings.deepagents_agent_name,
        cache=settings.runtime_cache(),
    )


def _deepagents_subagents(
    subagents: tuple[dict[str, Any] | Any, ...],
    backend: Any,
) -> list[SubAgent | CompiledSubAgent | AsyncSubAgent] | None:
    if not subagents:
        return None
    converted: list[SubAgent | CompiledSubAgent | AsyncSubAgent] = []
    for item in subagents:
        if not isinstance(item, dict):
            converted.append(cast("SubAgent | CompiledSubAgent | AsyncSubAgent", item))
            continue
        current = dict(item)
        resource_prefix = f"/subagents/{_resource_mount_name(current)}"
        current["skills"] = _mounted_resource_paths(
            current.get("skills"),
            current.get("skills_path"),
            f"{resource_prefix}/skills",
        )
        memory = _mounted_resource_paths(
            current.get("memory"),
            current.get("memory_path"),
            f"{resource_prefix}/memory",
        )
        if "graph_id" in current:
            allowed = {"name", "description", "graph_id", "url", "headers"}
        elif "runnable" in current:
            allowed = {"name", "description", "runnable"}
        else:
            if memory:
                current["middleware"] = [
                    *list(current.get("middleware") or []),
                    MemoryMiddleware(backend=backend, sources=memory),
                ]
            allowed = {
                "name",
                "description",
                "system_prompt",
                "tools",
                "model",
                "middleware",
                "interrupt_on",
                "skills",
                "permissions",
                "subagents",
            }
        converted.append(
            cast(
                "SubAgent | CompiledSubAgent | AsyncSubAgent",
                {
                    key: value
                    for key, value in current.items()
                    if key in allowed and value is not None
                },
            )
        )
    return converted


def _mounted_resource_paths(
    paths: Any,
    source_root: Any,
    mount_prefix: str,
) -> list[str] | None:
    raw_values = [paths] if isinstance(paths, str | Path) else list(paths or [])
    values = [str(path) for path in raw_values if str(path)]
    if not values:
        return None
    root = Path(str(source_root)).resolve() if source_root else None
    return [_mounted_resource_path(value, root, mount_prefix) for value in values]


def _mounted_resource_path(value: str, source_root: Path | None, mount_prefix: str) -> str:
    if value.startswith(f"{mount_prefix}/") or value == mount_prefix:
        return value
    if source_root is None:
        return value
    path = Path(value)
    try:
        relative = path.resolve().relative_to(source_root)
    except (OSError, ValueError):
        return value
    return PurePosixPath(mount_prefix, relative.as_posix()).as_posix()


def _subagent_resource_mounts(subagents: tuple[dict[str, Any] | Any, ...]) -> dict[str, str]:
    mounts: dict[str, str] = {}
    for item in subagents:
        if not isinstance(item, dict):
            continue
        resource_prefix = f"/subagents/{_resource_mount_name(item)}"
        if item.get("skills_path"):
            mounts[f"{resource_prefix}/skills/"] = str(item["skills_path"])
        if item.get("memory_path"):
            mounts[f"{resource_prefix}/memory/"] = str(item["memory_path"])
    return mounts


def _resource_mount_name(item: dict[str, Any]) -> str:
    raw = str(item.get("name") or item.get("id") or "agent").strip().lower()
    normalized = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in raw)
    return normalized.strip("-_") or "agent"
