from __future__ import annotations

import importlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.model_catalog import ModelCatalog
from deepagents_integration.extensions import (
    SelectablePathRegistry,
    build_builtin_tool_selection_middleware,
    discover_components,
    discover_memory,
    flatten_components,
    load_middleware_extensions,
    load_object_from_spec,
    load_tool_extensions,
)


@dataclass(frozen=True)
class RuntimeSelection:
    model_id: str | None = None


@dataclass(frozen=True)
class RuntimeResolution:
    agent: Mapping[str, Any]
    model_id: str | None
    subagents: tuple[Mapping[str, Any], ...]


def load_agent_spec(spec: str) -> Mapping[str, Any]:
    value = load_object_from_spec(spec)
    if not isinstance(value, Mapping):
        raise ValueError(f"Agent spec {spec!r} must resolve to a mapping")
    return value


def resolve_runtime(
    *,
    agent_spec: str,
    model_catalog: ModelCatalog | None,
    default_model_id: str | None,
    selection: RuntimeSelection | None = None,
) -> RuntimeResolution:
    agent = _resolve_agent_package(load_agent_spec(agent_spec))
    selected_model_id = selection.model_id if selection and selection.model_id else default_model_id
    if model_catalog is not None and selected_model_id is None:
        selected_model_id = model_catalog.default_model_id
    subagents = tuple(
        _build_subagent_spec(
            raw,
            model_catalog=model_catalog,
            default_model_id=selected_model_id,
            seen=(str(agent.get("id") or "main"),),
        )
        for raw in flatten_components(agent.get("subagents"))
    )
    return RuntimeResolution(
        agent=agent,
        model_id=selected_model_id,
        subagents=subagents,
    )


def runtime_options(*, model_catalog: ModelCatalog | None) -> dict[str, Any]:
    return (
        model_catalog.safe_options()
        if model_catalog is not None
        else {"default_model_id": "", "models": []}
    )


def _build_subagent_spec(
    raw: Any,
    *,
    model_catalog: ModelCatalog | None,
    default_model_id: str | None,
    seen: tuple[str, ...],
) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("subagent entries must be mappings")
    if "sandbox" in raw:
        raise ValueError("agent-level sandbox is not supported; configure sandbox globally")
    agent_id = str(raw.get("id") or raw.get("name") or "")
    if not agent_id:
        raise ValueError("subagent entries must define id or name")
    if agent_id in seen:
        raise ValueError(f"Recursive subagent cycle detected at {agent_id!r}")
    if "graph_id" in raw or raw.get("type") == "async":
        return {
            key: value
            for key, value in {
                "id": agent_id,
                "name": raw.get("name") or agent_id,
                "label": raw.get("label") or raw.get("name") or agent_id,
                "description": raw.get("description"),
                "graph_id": raw.get("graph_id"),
                "url": raw.get("url"),
                "headers": raw.get("headers"),
                "workspace": raw.get("workspace"),
            }.items()
            if value not in (None, "")
        }
    raw = _resolve_agent_package(raw)
    system_prompt = _resolve_system_prompt(raw)
    model_id = str(raw.get("model") or raw.get("model_id") or default_model_id or "")
    middleware = _resolve_component_list(
        raw.get("middleware"),
        raw.get("middleware_specs"),
        load_middleware_extensions,
    )
    tool_selection = build_builtin_tool_selection_middleware(
        allowlist=raw.get("builtin_tool_allowlist"),
        blocklist=raw.get("builtin_tool_blocklist"),
    )
    if tool_selection is not None:
        middleware.append(tool_selection)
    subagent: dict[str, Any] = {
        "id": agent_id,
        "name": raw.get("name") or agent_id,
        "label": raw.get("label") or raw.get("name") or agent_id,
        "description": raw.get("description") or "",
        "system_prompt": system_prompt,
        "tools": _resolve_component_list(
            raw.get("tools"),
            raw.get("tool_specs"),
            load_tool_extensions,
        ),
        "middleware": middleware,
        "skills": tuple(source.source_path for source in raw.get("skill_sources", ())),
        "permissions": tuple(raw.get("permissions") or ()),
        "workspace": raw.get("workspace") or "",
    }
    if model_catalog is not None and model_id:
        subagent["model"] = model_catalog.resolve(model_id)
        subagent["model_id"] = model_id
    elif model_id:
        subagent["model"] = model_id
        subagent["model_id"] = model_id
    nested = tuple(
        _build_subagent_spec(
            item,
            model_catalog=model_catalog,
            default_model_id=model_id or default_model_id,
            seen=(*seen, agent_id),
        )
        for item in flatten_components(raw.get("subagents"))
    )
    if nested:
        subagent["subagents"] = nested
    return {key: value for key, value in subagent.items() if value not in (None, "", (), [])}

def _resolve_component_list(
    direct: Any,
    specs: Any,
    loader: Any,
) -> list[Any]:
    resolved = flatten_components(direct)
    if specs:
        resolved.extend(loader(tuple(specs)))
    return resolved


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if not isinstance(value, list | tuple) or not all(isinstance(item, str) for item in value):
        raise ValueError("Expected a string or list of strings")
    return tuple(item.strip() for item in value if item.strip())


def _optional_string_tuple(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    return _string_tuple(value)


def _mapping_tuple(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple) or not all(isinstance(item, Mapping) for item in value):
        raise ValueError("Expected a list of mappings")
    return tuple(value)


def _resolve_agent_package(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    root = _agent_root(raw)
    package_name = _package_name(root)
    resolved = _normalize_agent_fields(raw)
    if isinstance(raw.get("system_prompt"), Path) and "system_prompt_path" not in raw:
        resolved["system_prompt_path"] = raw["system_prompt"]
    if root is not None and package_name:
        subagent_package = f"{package_name}.subagents"
        resolved["_agent_root"] = root
        resolved["_subagent_package"] = subagent_package
        resolved["tools"] = _resolve_python_selection(
            package=f"{package_name}.tools",
            value=raw.get("tools"),
            names=("TOOLS", "TOOL"),
        )
        resolved["middleware"] = _resolve_python_selection(
            package=f"{package_name}.middleware",
            value=raw.get("middleware"),
            names=("MIDDLEWARE", "MIDDLEWARE_ITEM"),
        )
        resolved["hooks"] = _resolve_hooks(
            package=package_name,
            raw_hooks=raw.get("hooks"),
        )
        resolved["skill_sources"] = _resolve_skill_selection(root, raw.get("skills"))
        resolved["memory"] = _resolve_memory_selection(root, raw.get("memory"))
        resolved["subagents"] = _resolve_subagent_selection(
            package=subagent_package,
            value=raw.get("subagents"),
        )
    return resolved


def _normalize_agent_fields(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **raw,
        "workspace": str(raw.get("workspace") or ""),
        "permissions": _mapping_tuple(raw.get("permissions")),
        "builtin_tool_allowlist": _optional_string_tuple(
            raw.get("builtin_tools") or raw.get("builtin_tool_allowlist")
        ),
        "builtin_tool_blocklist": _string_tuple(
            raw.get("disabled_builtin_tools") or raw.get("builtin_tool_blocklist")
        ),
    }


def _agent_root(raw: Mapping[str, Any]) -> Path | None:
    if raw.get("root"):
        return Path(raw["root"])
    prompt_path = raw.get("system_prompt_path") or raw.get("system_prompt")
    if isinstance(prompt_path, Path):
        if prompt_path.parent.name == "prompts":
            return prompt_path.parent.parent
        return prompt_path.parent
    return None


def _package_name(root: Path | None) -> str:
    if root is None:
        return ""
    parts = root.resolve().parts
    if "backend" not in parts:
        return root.name
    index = parts.index("backend")
    package_parts = parts[index + 1 :]
    return ".".join(package_parts)


def _resolve_python_selection(
    *,
    package: str,
    value: Any,
    names: tuple[str, ...],
) -> list[Any]:
    if value is None:
        return []
    if value == "*":
        return discover_components(package, names=names)
    if isinstance(value, str):
        return _exports_from_module(f"{package}.{value}", names)
    resolved: list[Any] = []
    for item in flatten_components(value):
        if isinstance(item, str):
            resolved.extend(_exports_from_module(f"{package}.{item}", names))
        else:
            resolved.append(item)
    return resolved


def _resolve_hooks(*, package: str, raw_hooks: Any) -> dict[str, tuple[Any, ...]]:
    hooks = raw_hooks if isinstance(raw_hooks, Mapping) else {}
    return {
        "run_input": tuple(
            _resolve_python_selection(
                package=f"{package}.hooks",
                value=hooks.get("run_input", ()),
                names=("RUN_INPUT_HOOKS",),
            )
        ),
        "upload": tuple(
            _resolve_python_selection(
                package=f"{package}.hooks",
                value=hooks.get("upload", ()),
                names=("UPLOAD_HOOKS",),
            )
        ),
    }


def _resolve_skill_selection(root: Path, value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    registry = SelectablePathRegistry.from_directory(root / "skills", source_prefix="/skills")
    if value == "*":
        return tuple(registry.all())
    if isinstance(value, str):
        return tuple(registry.select([value]))
    if all(isinstance(item, str) for item in flatten_components(value)):
        return tuple(registry.select([str(item) for item in flatten_components(value)]))
    return tuple(flatten_components(value))


def _resolve_memory_selection(root: Path, value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    registry = discover_memory(root / "memory")
    if value == "*":
        return tuple(str(item) for item in registry.all())
    if isinstance(value, str):
        return tuple(str(item) for item in registry.select([value]))
    flattened = flatten_components(value)
    if all(isinstance(item, str) for item in flattened):
        return tuple(str(item) for item in registry.select([str(item) for item in flattened]))
    return tuple(str(item) for item in flattened)


def _resolve_subagent_selection(*, package: str, value: Any) -> list[Any]:
    if value is None:
        return []
    if value == "*":
        return discover_components(package, names=("SUBAGENTS", "SUBAGENT"))
    resolved: list[Any] = []
    for item in flatten_components(value):
        if isinstance(item, str):
            resolved.extend(_exports_from_module(f"{package}.{item}", ("SUBAGENTS", "SUBAGENT")))
        else:
            resolved.append(item)
    return resolved

def _exports_from_module(module_name: str, names: tuple[str, ...]) -> list[Any]:
    module = importlib.import_module(module_name)
    exported: list[Any] = []
    for name in names:
        if hasattr(module, name):
            exported.extend(flatten_components(getattr(module, name)))
    if not exported:
        raise ValueError(f"{module_name} does not export any of: {', '.join(names)}")
    return exported


def _resolve_system_prompt(raw: Mapping[str, Any]) -> str:
    if isinstance(raw.get("system_prompt"), str):
        return str(raw["system_prompt"])
    prompt_path = raw.get("system_prompt_path")
    if prompt_path:
        return Path(prompt_path).read_text(encoding="utf-8").strip()
    raise ValueError(f"subagent {raw.get('id') or raw.get('name')!r} must define system_prompt")
