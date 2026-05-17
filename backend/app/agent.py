from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, cast

from deepagents import create_deep_agent
from deepagents.middleware.async_subagents import AsyncSubAgent
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
    attachments: tuple[dict[str, Any], ...] = ()

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
    sandbox_config = SandboxConfig.from_mapping(sandbox_settings)
    permissions = list(agent["permissions"])
    return create_deep_agent(
        model=build_model(settings, str(selected_model_id) if selected_model_id else None),
        tools=list(agent["tools"]),
        middleware=middleware,
        skills=list(agent["skills"]) or None,
        memory=list(agent["memory"]) or None,
        permissions=permissions,
        subagents=_deepagents_subagents(tuple(agent["subagents"])),
        system_prompt=agent["system_prompt"],
        context_schema=DeepAgentsRunContext,
        checkpointer=settings.runtime_checkpointer(),
        store=settings.runtime_store(),
        backend=resolve_backend(sandbox_config),
        interrupt_on=cast(Any, settings.deepagents_interrupt_on or None),
        debug=settings.deepagents_debug,
        name=settings.deepagents_agent_name,
        cache=settings.runtime_cache(),
    )


def _deepagents_subagents(
    subagents: tuple[dict[str, Any] | Any, ...],
) -> list[SubAgent | CompiledSubAgent | AsyncSubAgent] | None:
    if not subagents:
        return None
    converted: list[SubAgent | CompiledSubAgent | AsyncSubAgent] = []
    for item in subagents:
        if not isinstance(item, dict):
            converted.append(cast("SubAgent | CompiledSubAgent | AsyncSubAgent", item))
            continue
        if "graph_id" in item:
            allowed = {"name", "description", "graph_id", "url", "headers"}
        elif "runnable" in item:
            allowed = {"name", "description", "runnable"}
        else:
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
                {key: value for key, value in item.items() if key in allowed and value is not None},
            )
        )
    return converted
