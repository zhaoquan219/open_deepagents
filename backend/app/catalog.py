from __future__ import annotations

import json
import os
from importlib import import_module
from pathlib import Path
from typing import Any

from langchain_openai import ChatOpenAI

from app.runtime.extensions import build_permissions as resolve_permissions
from app.settings import BACKEND_ROOT, Settings, import_from_spec

MODEL_OPTION_KEYS = {
    "api_key",
    "base_url",
    "default_headers",
    "default_query",
    "max_retries",
    "organization",
    "timeout",
}
MODEL_KEYS = {"name", "model", "temperature", "extra_body"}


def load_model_catalog(settings: Settings) -> dict[str, Any]:
    path = _path(settings.deepagents_model_config_path or "./models.json")
    if not path.exists():
        return {"model": "", "provider": {}}
    return dict(json.loads(path.read_text(encoding="utf-8")))


def validate_model_catalog(
    catalog: dict[str, Any],
    *,
    selected_model_id: str | None,
    production: bool,
) -> None:
    providers = catalog.get("provider")
    selected = selected_model_id or str(catalog.get("model") or "")
    if not isinstance(providers, dict) or not providers:
        raise ValueError("models.json must define at least one provider")
    if not selected or "/" not in selected:
        raise ValueError("models.json selected model must use provider/model format")
    provider_id, _, model_key = selected.partition("/")
    if provider_id not in providers:
        raise ValueError(f"Unknown model provider in selected model: {provider_id}")

    for current_provider_id, provider in providers.items():
        if not isinstance(provider, dict):
            raise ValueError(f"models.json provider {current_provider_id} must be an object")
        _validate_provider(current_provider_id, provider, production)

    selected_models = dict(dict(providers.get(provider_id) or {}).get("models") or {})
    if model_key not in selected_models:
        raise ValueError(f"Unknown selected model: {selected}")


def default_model_id(settings: Settings, override: str | None = None) -> str:
    catalog = load_model_catalog(settings)
    selected = (override or "").strip() or str(catalog.get("model") or "")
    validate_model_catalog(
        catalog,
        selected_model_id=selected or None,
        production=settings.is_production(),
    )
    return selected


def model_options(settings: Settings) -> dict[str, Any]:
    catalog = load_model_catalog(settings)
    validate_model_catalog(catalog, selected_model_id=None, production=False)
    records: list[dict[str, str]] = []
    for provider_id, provider in dict(catalog.get("provider") or {}).items():
        for model_id, model in dict(provider.get("models") or {}).items():
            records.append(
                {
                    "id": f"{provider_id}/{model_id}",
                    "name": str(model.get("name") or model_id),
                    "provider": str(provider_id),
                    "provider_name": str(provider.get("name") or provider_id),
                    "model": str(model.get("model") or model_id),
                }
            )
    return {
        "default_model_id": str(catalog.get("model") or records[0]["id"] if records else ""),
        "models": records,
    }


def build_model(settings: Settings, model_id: str | None = None) -> Any:
    catalog = load_model_catalog(settings)
    selected = default_model_id(settings, model_id)
    provider_id, _, model_key = selected.partition("/")
    provider = dict(dict(catalog.get("provider") or {}).get(provider_id) or {})
    model = dict(dict(provider.get("models") or {}).get(model_key) or {})
    if not model:
        raise ValueError(f"Unknown model selection: {selected}")
    options = dict(provider.get("options") or {})
    unknown_options = set(options) - MODEL_OPTION_KEYS
    if unknown_options:
        raise ValueError(f"Unknown model provider option(s): {', '.join(sorted(unknown_options))}")
    runtime_options = _resolve_env(
        {**options, **{key: value for key, value in model.items() if key != "name"}},
        public=False,
    )
    runtime_options.setdefault("model", model.get("model") or model_key)
    return ChatOpenAI(**runtime_options)


def resolve_agent(settings: Settings) -> dict[str, Any]:
    root, package_root = _import_agent_mapping(settings.deepagents_main_agent)
    if not isinstance(root, dict):
        raise ValueError("DEEPAGENTS_MAIN_AGENT must resolve to a dict")
    return _resolve_agent_dict(root, seen=set(), package_root=package_root)


def resolve_components(value: Any, *, package: str, export: str) -> list[Any]:
    resolved: list[Any] = []
    for item in _items(value):
        component = _resolve_component(item, package, export)
        resolved.extend(component if isinstance(component, list | tuple) else [component])
    return resolved


def read_text(value: Any) -> str:
    if isinstance(value, Path) or (isinstance(value, str) and Path(value).suffix):
        path = _path(value)
        if path.exists():
            return path.read_text(encoding="utf-8")
    return str(value or "")


def _validate_provider(provider_id: str, provider: dict[str, Any], production: bool) -> None:
    options = provider.get("options") or {}
    models = provider.get("models")
    if not isinstance(options, dict):
        raise ValueError(f"models.json provider {provider_id}.options must be an object")
    unknown_options = set(options) - MODEL_OPTION_KEYS
    if unknown_options:
        raise ValueError(
            "Unknown model provider option(s) at "
            f"provider.{provider_id}.options: {', '.join(sorted(unknown_options))}"
        )
    if (
        production
        and "api_key" in options
        and _resolve_env(options.get("api_key"), public=False) == ""
    ):
        raise ValueError(f"Selected model provider {provider_id} resolves an empty API key")
    if not isinstance(models, dict) or not models:
        raise ValueError(f"models.json provider {provider_id}.models must be non-empty")
    for model_id, model in models.items():
        if not isinstance(model, dict):
            raise ValueError(
                f"models.json provider.{provider_id}.models.{model_id} must be an object"
            )
        unknown_model_keys = set(model) - MODEL_KEYS
        if unknown_model_keys:
            raise ValueError(
                "Unknown model field(s) at "
                f"provider.{provider_id}.models.{model_id}: "
                f"{', '.join(sorted(unknown_model_keys))}"
            )


def _resolve_agent_dict(
    raw: dict[str, Any],
    *,
    seen: set[str],
    package_root: Path,
) -> dict[str, Any]:
    agent_id = str(raw.get("id") or raw.get("name") or id(raw))
    if agent_id in seen:
        raise ValueError(f"Cyclic subagent reference: {agent_id}")
    next_seen = {*seen, agent_id}
    return {
        "id": agent_id,
        "name": raw.get("name") or agent_id,
        "description": raw.get("description") or "",
        "system_prompt": read_text(raw.get("system_prompt") or ""),
        "tools": resolve_components(raw.get("tools"), package="agents.tools", export="TOOLS"),
        "middleware": resolve_components(
            raw.get("middleware"),
            package="agents.middleware",
            export="MIDDLEWARE",
        ),
        "builtin_tool_allowlist": _builtin_tuple(
            raw.get("builtin_tool_allowlist") or raw.get("builtin_tools")
        ),
        "builtin_tool_blocklist": _builtin_tuple(
            raw.get("builtin_tool_blocklist") or raw.get("disabled_builtin_tools")
        ),
        "skills": _registry_entries(raw.get("skills"), package_root, "skills", skill=True),
        "memory": _registry_entries(raw.get("memory"), package_root, "memory", skill=False),
        "permissions": resolve_permissions(tuple(_items(raw.get("permissions")))),
        "subagents": _resolve_subagents(raw.get("subagents"), package_root, next_seen),
        "model": raw.get("model"),
    }


def _resolve_subagents(
    value: Any,
    package_root: Path,
    seen: set[str],
) -> list[dict[str, Any] | Any]:
    resolved: list[dict[str, Any] | Any] = []
    for item in _items(value):
        items = _discover_subagent_specs(package_root) if item == "*" else [item]
        for current in items:
            child, child_root = _load_subagent_item(current, package_root)
            resolved.append(_resolve_agent_dict(child, seen=seen, package_root=child_root))
    return resolved


def _load_subagent_item(item: Any, package_root: Path) -> tuple[Any, Path]:
    if isinstance(item, dict):
        return item, package_root
    if isinstance(item, str) and ":" in item:
        return _import_agent_mapping(item)
    path = _package_path(package_root / "subagents", item)
    if path.is_dir() and (path / "__init__.py").is_file():
        return _import_agent_mapping(f"{path / '__init__.py'}:SUBAGENT")
    if path.suffix == ".py" and path.is_file():
        return _import_agent_mapping(f"{path}:SUBAGENT")
    raise ValueError(f"Unknown subagent selection: {item}")


def _registry_entries(value: Any, package_root: Path, folder: str, *, skill: bool) -> list[str]:
    root = (package_root / folder).resolve()
    ids = _registry_ids(root, skill=skill)
    selected: list[str] = []
    for item in _items(value):
        if item == "*":
            selected.extend(_registry_path(root, item_id, skill=skill) for item_id in ids)
        elif isinstance(item, str | Path) and str(item) in ids:
            selected.append(_registry_path(root, str(item), skill=skill))
        else:
            selected.append(str(_package_path(root, item)))
    return list(dict.fromkeys(selected))


def _registry_ids(root: Path, *, skill: bool) -> tuple[str, ...]:
    if not root.exists():
        return ()
    if skill:
        return tuple(sorted(path.name for path in root.iterdir() if (path / "SKILL.md").is_file()))
    return tuple(sorted(path.stem for path in root.glob("*.md") if path.is_file()))


def _registry_path(root: Path, item_id: str, *, skill: bool) -> str:
    return str(root / (item_id if skill else f"{item_id}.md"))


def _discover_subagent_specs(package_root: Path) -> list[str]:
    root = package_root / "subagents"
    if not root.exists():
        return []
    return [
        f"{path / '__init__.py'}:SUBAGENT"
        for path in sorted(root.iterdir())
        if path.is_dir() and (path / "__init__.py").is_file()
    ]


def _import_agent_mapping(spec: str) -> tuple[Any, Path]:
    module_name, separator, attr = spec.partition(":")
    if not separator or not attr:
        raise ValueError(f"Import spec must be module:attribute: {spec}")
    if module_name.endswith(".py") or "/" in module_name:
        path = _path(module_name).resolve()
        return import_from_spec(spec), path.parent
    module = import_module(module_name)
    return getattr(module, attr), Path(module.__file__ or "").resolve().parent


def _resolve_component(item: Any, package: str, export: str) -> Any:
    if callable(item) or isinstance(item, dict):
        return item
    if item == "*":
        return list(getattr(__import__(package, fromlist=[export]), export))
    if isinstance(item, str) and ":" in item:
        return import_from_spec(item)
    return item


def _items(value: Any) -> list[Any]:
    if value in (None, "", []):
        return []
    if isinstance(value, list | tuple):
        return list(value)
    return [value]


def _builtin_tuple(value: Any) -> tuple[str, ...] | None:
    items = tuple(str(item).strip() for item in _items(value) if str(item).strip())
    return items or None


def _path(value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else BACKEND_ROOT / path


def _package_path(root: Path, value: Any) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    local_path = (root / path).resolve()
    return local_path if local_path.exists() else _path(path)


def _resolve_env(value: Any, *, public: bool) -> Any:
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return "" if public else os.environ.get(value[2:-1], "")
    if isinstance(value, dict):
        return {key: _resolve_env(item, public=public) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_env(item, public=public) for item in value]
    return value
