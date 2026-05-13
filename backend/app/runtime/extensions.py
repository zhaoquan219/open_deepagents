from __future__ import annotations

import hashlib
import importlib
import importlib.util
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any, Literal, cast

from deepagents import FilesystemPermission
from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend, StateBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileUploadResponse,
    WriteResult,
)

from app.path_utils import app_path, split_import_spec


@dataclass(frozen=True)
class SandboxConfig:
    kind: str = "state"
    root_dir: str | None = None
    skills_root_dir: str | None = None
    uploads_root_dir: str | None = None
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
        skills_root_dir = raw.get("skills_root_dir")
        if skills_root_dir is not None and not isinstance(skills_root_dir, str):
            raise ValueError("sandbox.skills_root_dir must be a string when provided")
        uploads_root_dir = raw.get("uploads_root_dir")
        if uploads_root_dir is not None and not isinstance(uploads_root_dir, str):
            raise ValueError("sandbox.uploads_root_dir must be a string when provided")
        config = cls(
            kind=kind,
            root_dir=root_dir,
            skills_root_dir=skills_root_dir,
            uploads_root_dir=uploads_root_dir,
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


def build_permissions(
    permission_specs: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
) -> list[FilesystemPermission]:
    permissions: list[FilesystemPermission] = []
    for spec in permission_specs:
        _reject_builtin_tool_permission_spec(spec)
        operations = _permission_operations(spec)
        paths = list(spec.get("paths") or [])
        if not paths:
            raise ValueError("permissions entries must define paths")
        permissions.append(
            FilesystemPermission(
                operations=list(operations),
                paths=_expanded_paths(paths),
                mode=_permission_mode(spec),
            )
        )
    permissions.extend(
        (
            FilesystemPermission(operations=["read"], paths=["/**"], mode="deny"),
            FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
        )
    )
    return permissions


def resolve_backend(config: SandboxConfig) -> BackendProtocol | Any:
    base_backend: BackendProtocol | Any
    if config.kind == "state":
        base_backend = StateBackend()
        return _with_mounted_roots(base_backend, config)
    if config.kind == "filesystem":
        base_backend = FilesystemBackend(root_dir=config.root_dir, virtual_mode=config.virtual_mode)
        return _with_mounted_roots(base_backend, config)
    if config.kind == "local_shell":
        base_backend = LocalShellBackend(
            root_dir=config.root_dir,
            virtual_mode=config.virtual_mode,
            timeout=config.timeout,
            max_output_bytes=config.max_output_bytes,
            env=dict(config.env) or None,
            inherit_env=config.inherit_env,
        )
        return _with_mounted_roots(base_backend, config)
    backend = load_object_from_spec(config.backend_spec or "")
    if callable(backend) and not isinstance(backend, BackendProtocol):
        backend = backend()
    if not callable(backend) and not isinstance(backend, BackendProtocol):
        raise TypeError(
            "Custom backend specs must resolve to a BackendProtocol instance, "
            "class, or backend factory"
        )
    return _with_mounted_roots(backend, config)


def _with_mounted_roots(backend: BackendProtocol | Any, config: SandboxConfig) -> BackendProtocol:
    routes: dict[str, Any] = {}
    if config.skills_root_dir:
        routes["/skills/"] = ReadOnlyBackend(
            FilesystemBackend(root_dir=config.skills_root_dir, virtual_mode=True)
        )
    if config.uploads_root_dir:
        routes["/uploads/"] = ReadOnlyBackend(
            FilesystemBackend(root_dir=config.uploads_root_dir, virtual_mode=True)
        )
    if not routes:
        return backend
    return CompositeBackend(default=backend, routes=routes)


class ReadOnlyBackend:
    def __init__(self, backend: BackendProtocol) -> None:
        self._backend = backend

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)

    def write(self, file_path: str, content: str) -> WriteResult:
        del content
        return WriteResult(error="read-only route", path=file_path)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return self.write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        del old_string, new_string, replace_all
        return EditResult(error="read-only route", path=file_path)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return self.edit(file_path, old_string, new_string, replace_all)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [
            FileUploadResponse(path=file_path, error="permission_denied")
            for file_path, _ in files
        ]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self.upload_files(files)


def load_object_from_spec(spec: str) -> Any:
    try:
        module_name, attr = split_import_spec(spec)
    except ValueError as exc:
        raise ValueError(
            f"Invalid import spec {spec!r}; expected '<module-or-path>:<attribute>'"
        ) from exc
    module = _import_module_or_file(module_name)
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise ValueError(f"Import target {spec!r} does not define attribute {attr!r}") from exc


def _expanded_paths(paths: list[str]) -> list[str]:
    expanded: list[str] = []
    for path in paths:
        normalized = _normalize_path(str(path), trailing_slash=str(path).endswith(("/", "\\")))
        expanded.append(normalized)
        child_glob = f"{normalized.rstrip('/')}/**"
        if not any(char in normalized for char in "*?[") and child_glob not in expanded:
            expanded.append(child_glob)
    return expanded


def _reject_builtin_tool_permission_spec(spec: Mapping[str, Any]) -> None:
    if "builtin_tools" in spec:
        raise ValueError(
            "permissions[].builtin_tools is not supported; use native "
            "permissions[].operations and paths instead"
        )


def _permission_operations(spec: Mapping[str, Any]) -> tuple[Literal["read", "write"], ...]:
    operations = _strings(spec.get("operations"))
    if not operations:
        raise ValueError("permissions entries must define operations")
    unknown = sorted(set(operations) - {"read", "write"})
    if unknown:
        raise ValueError(f"Unknown filesystem operation(s): {', '.join(unknown)}")
    normalized: list[Literal["read", "write"]] = []
    for operation in operations:
        typed = cast(Literal["read", "write"], operation)
        if typed not in normalized:
            normalized.append(typed)
    return tuple(normalized)


def _permission_mode(spec: Mapping[str, Any]) -> Literal["allow", "deny"]:
    mode = str(spec.get("mode", "allow"))
    if mode not in {"allow", "deny"}:
        raise ValueError("permissions[].mode must be 'allow' or 'deny'")
    return cast(Literal["allow", "deny"], mode)


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, list | tuple) and all(isinstance(item, str) for item in value):
        return tuple(item.strip() for item in value if item.strip())
    raise ValueError("Expected a string or list of strings")


def _import_module_or_file(module_name: str) -> ModuleType:
    path = app_path(module_name, Path.cwd())
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
    normalized = PurePosixPath(path.replace("\\", "/").strip()).as_posix()
    if not normalized.startswith("/"):
        normalized = f"/{normalized.lstrip('/')}"
    if trailing_slash:
        return f"{normalized.rstrip('/')}/"
    return normalized.rstrip("/") if normalized != "/" else normalized
