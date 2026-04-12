from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

from deepagents import FilesystemPermission
from deepagents.backends import FilesystemBackend, LocalShellBackend, StateBackend
from deepagents.backends.protocol import BackendProtocol

from .config import SandboxConfig


def load_object_from_spec(spec: str) -> Any:
    """Load ``module:attribute`` or ``/path/to/file.py:attribute`` targets."""

    module_name, separator, attribute = spec.partition(":")
    if not separator or not attribute:
        raise ValueError(f"Invalid import spec {spec!r}; expected '<module-or-path>:<attribute>'")

    module = _import_module_or_file(module_name)
    try:
        return getattr(module, attribute)
    except AttributeError as exc:
        raise ValueError(f"Import target {spec!r} does not define attribute {attribute!r}") from exc


def load_tool_extensions(tool_specs: list[str] | tuple[str, ...]) -> list[Any]:
    return _flatten_loaded_specs(tool_specs)


def load_middleware_extensions(middleware_specs: list[str] | tuple[str, ...]) -> list[Any]:
    return _flatten_loaded_specs(middleware_specs)


def build_permissions(
    permission_specs: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
) -> list[FilesystemPermission]:
    permissions: list[FilesystemPermission] = []
    for spec in permission_specs:
        permissions.append(
            FilesystemPermission(
                operations=list(spec["operations"]),
                paths=list(spec["paths"]),
                mode=spec.get("mode", "allow"),
            )
        )
    return permissions


def resolve_backend(config: SandboxConfig) -> BackendProtocol | Any:
    """Resolve a DeepAgents backend without inventing a parallel abstraction."""

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
    backend_extension = _materialize_backend_extension(load_object_from_spec(config.backend_spec or ""))
    if not callable(backend_extension) and not isinstance(backend_extension, BackendProtocol):
        raise TypeError("Custom backend specs must resolve to a BackendProtocol instance, class, or backend factory")
    return backend_extension


def _flatten_loaded_specs(specs: list[str] | tuple[str, ...]) -> list[Any]:
    loaded: list[Any] = []
    for spec in specs:
        value = load_object_from_spec(spec)
        if isinstance(value, (list, tuple)):
            loaded.extend(value)
        else:
            loaded.append(value)
    return loaded


def _import_module_or_file(module_name: str) -> ModuleType:
    potential_path = Path(module_name)
    if potential_path.suffix == ".py" and potential_path.exists():
        return _load_module_from_path(potential_path)
    return importlib.import_module(module_name)


def _load_module_from_path(path: Path) -> ModuleType:
    resolved = path.resolve()
    digest = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:12]
    module_name = f"deepagents_extension_{digest}"
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise ValueError(f"Unable to import Python module from {resolved}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _materialize_backend_extension(extension: Any) -> Any:
    if isinstance(extension, BackendProtocol):
        return extension
    if inspect.isclass(extension):
        return extension()
    if callable(extension):
        try:
            signature = inspect.signature(extension)
        except (TypeError, ValueError):
            return extension
        required_params = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.default is inspect.Signature.empty
            and parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
        ]
        return extension() if not required_params else extension
    return extension
