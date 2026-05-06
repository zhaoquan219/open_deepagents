from __future__ import annotations

import hashlib
import importlib
import importlib.util
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from deepagents import FilesystemPermission
from deepagents.backends import FilesystemBackend, LocalShellBackend, StateBackend
from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse

BUILTIN_TOOL_NAMES = frozenset(
    {"write_todos", "ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute", "task"}
)


@dataclass(frozen=True)
class SandboxConfig:
    kind: str = "state"
    root_dir: str | None = None
    virtual_mode: bool | None = None
    timeout: int = 120
    max_output_bytes: int = 100_000
    inherit_env: bool = False
    env: dict[str, str] = field(default_factory=dict)
    backend_spec: str | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> SandboxConfig:
        raw = raw or {}
        kind = raw.get("kind", "state")
        if kind not in {"state", "filesystem", "local_shell", "custom"}:
            raise ValueError(f"Unsupported sandbox kind: {kind!r}")
        env = raw.get("env", {}) or {}
        if not isinstance(env, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in env.items()
        ):
            raise ValueError("sandbox.env must be a mapping of string keys and string values")
        timeout = int(raw.get("timeout", 120))
        max_output_bytes = int(raw.get("max_output_bytes", 100_000))
        if timeout <= 0 or max_output_bytes <= 0:
            raise ValueError("sandbox timeout and max_output_bytes must be greater than 0")
        root_dir = raw.get("root_dir")
        if root_dir is not None and not isinstance(root_dir, str):
            raise ValueError("sandbox.root_dir must be a string when provided")
        config = cls(
            kind=kind,
            root_dir=root_dir,
            virtual_mode=raw.get("virtual_mode"),
            timeout=timeout,
            max_output_bytes=max_output_bytes,
            inherit_env=bool(raw.get("inherit_env", False)),
            env=dict(env),
            backend_spec=raw.get("backend_spec"),
        )
        if config.kind == "custom" and not config.backend_spec:
            raise ValueError("sandbox.backend_spec is required when sandbox.kind='custom'")
        return config


def build_builtin_tool_selection_middleware(
    *,
    allowlist: Any,
    blocklist: Any,
) -> AgentMiddleware[Any, Any, Any] | None:
    allowed = None if allowlist is None else frozenset(_strings(allowlist))
    blocked = frozenset(_strings(blocklist))
    if allowed is None and not blocked:
        return None
    return BuiltinToolSelectionMiddleware(allowed, blocked)


def build_permissions(
    permission_specs: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
) -> list[FilesystemPermission]:
    permissions = [
        FilesystemPermission(
            operations=list(spec["operations"]),
            paths=_expanded_paths(list(spec["paths"])),
            mode=spec.get("mode", "allow"),
        )
        for spec in permission_specs
    ]
    permissions.extend(
        (
            FilesystemPermission(operations=["read"], paths=["/**"], mode="deny"),
            FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
        )
    )
    return permissions


def resolve_backend(config: SandboxConfig) -> BackendProtocol | Any:
    if config.kind == "state":
        return StateBackend()
    if config.kind == "filesystem":
        return FilesystemBackend(root_dir=config.root_dir, virtual_mode=config.virtual_mode)
    if config.kind == "local_shell":
        return LocalShellBackend(
            root_dir=config.root_dir,
            virtual_mode=config.virtual_mode,
            timeout=config.timeout,
            max_output_bytes=config.max_output_bytes,
            env=dict(config.env) or None,
            inherit_env=config.inherit_env,
        )
    backend = load_object_from_spec(config.backend_spec or "")
    if callable(backend) and not isinstance(backend, BackendProtocol):
        backend = backend()
    if not callable(backend) and not isinstance(backend, BackendProtocol):
        raise TypeError(
            "Custom backend specs must resolve to a BackendProtocol instance, "
            "class, or backend factory"
        )
    return backend


def load_object_from_spec(spec: str) -> Any:
    module_name, separator, attr = spec.partition(":")
    if not separator or not attr:
        raise ValueError(f"Invalid import spec {spec!r}; expected '<module-or-path>:<attribute>'")
    module = _import_module_or_file(module_name)
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise ValueError(f"Import target {spec!r} does not define attribute {attr!r}") from exc


class BuiltinToolSelectionMiddleware(AgentMiddleware[Any, Any, Any]):
    def __init__(self, allowlist: frozenset[str] | None, blocklist: frozenset[str]) -> None:
        self._allowlist = allowlist
        self._blocklist = blocklist

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(request.override(tools=self._filter_tools(request.tools)))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(request.override(tools=self._filter_tools(request.tools)))

    def _filter_tools(self, tools: list[Any]) -> list[Any]:
        return [tool for tool in tools if self._keeps_tool(tool)]

    def _keeps_tool(self, tool: Any) -> bool:
        name = _tool_name(tool)
        if name not in BUILTIN_TOOL_NAMES:
            return True
        if self._allowlist is not None and name not in self._allowlist:
            return False
        return name not in self._blocklist


def _expanded_paths(paths: list[str]) -> list[str]:
    expanded: list[str] = []
    for path in paths:
        normalized = _normalize_path(str(path), trailing_slash=path.endswith("/"))
        expanded.append(normalized)
        child_glob = f"{normalized.rstrip('/')}/**"
        if not any(char in normalized for char in "*?[") and child_glob not in expanded:
            expanded.append(child_glob)
    return expanded


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, list | tuple) and all(isinstance(item, str) for item in value):
        return tuple(item.strip() for item in value if item.strip())
    raise ValueError("Expected a string or list of strings")


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        return str(tool.get("name") or "")
    return str(getattr(tool, "name", "") or getattr(tool, "__name__", "") or "")


def _import_module_or_file(module_name: str) -> ModuleType:
    path = Path(module_name)
    if path.suffix == ".py" and path.exists():
        return _load_module_from_path(path)
    return importlib.import_module(module_name)


def _load_module_from_path(path: Path) -> ModuleType:
    resolved = path.resolve()
    name = f"app_runtime_extension_{hashlib.sha1(str(resolved).encode()).hexdigest()[:12]}"
    spec = importlib.util.spec_from_file_location(name, resolved)
    if spec is None or spec.loader is None:
        raise ValueError(f"Unable to import Python module from {resolved}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normalize_path(path: str, trailing_slash: bool = False) -> str:
    normalized = path.replace("\\", "/")
    if not normalized.startswith("/"):
        normalized = f"/{normalized.lstrip('/')}"
    if trailing_slash:
        return f"{normalized.rstrip('/')}/"
    return normalized.rstrip("/") if normalized != "/" else normalized
