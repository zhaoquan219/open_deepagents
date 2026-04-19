from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
import logging
import pkgutil
import shutil
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

from deepagents import FilesystemPermission
from deepagents.backends import FilesystemBackend, LocalShellBackend, StateBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse

from .config import SandboxConfig, SkillSourceConfig

logger = logging.getLogger(__name__)
DEEPAGENTS_BUILTIN_TOOLS = frozenset(
    {
        "write_todos",
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "execute",
        "task",
    }
)


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


def flatten_components(value: Any) -> list[Any]:
    """Flatten registry exports while preserving non-list objects."""

    if value is None:
        return []
    if isinstance(value, list | tuple):
        flattened: list[Any] = []
        for item in value:
            flattened.extend(flatten_components(item))
        return flattened
    return [value]


def discover_components(
    package: str,
    *,
    names: tuple[str, ...],
    recursive: bool = False,
    exclude: tuple[str, ...] = (),
) -> list[Any]:
    """Discover component exports from child modules of a package."""

    module = importlib.import_module(package)
    package_paths = getattr(module, "__path__", None)
    if package_paths is None:
        return _exports_from_module(module, names)

    excluded = set(exclude)
    discovered: list[Any] = []
    for module_info in pkgutil.iter_modules(package_paths, f"{package}."):
        short_name = module_info.name.rsplit(".", maxsplit=1)[-1]
        if short_name.startswith("_") or short_name in excluded:
            continue
        child = importlib.import_module(module_info.name)
        discovered.extend(_exports_from_module(child, names))
        if recursive and module_info.ispkg:
            discovered.extend(
                discover_components(
                    module_info.name,
                    names=names,
                    recursive=True,
                    exclude=exclude,
                )
            )
    return discovered


def discover_skills(path_or_file: str | Path) -> SelectablePathRegistry:
    root = _directory_for_registry(path_or_file)
    return SelectablePathRegistry.from_directory(root, source_prefix="/skills")


def discover_memory(path_or_file: str | Path) -> SelectablePathRegistry:
    root = _directory_for_registry(path_or_file)
    return SelectablePathRegistry.from_markdown_directory(root)


def build_builtin_tool_selection_middleware(
    *,
    allowlist: tuple[str, ...] | None,
    blocklist: tuple[str, ...],
) -> AgentMiddleware[Any, Any, Any] | None:
    if allowlist is None and not blocklist:
        return None
    return BuiltinToolSelectionMiddleware(
        allowlist=frozenset(allowlist) if allowlist is not None else None,
        blocklist=frozenset(blocklist),
    )


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
    backend_extension = _materialize_backend_extension(
        load_object_from_spec(config.backend_spec or "")
    )
    if not callable(backend_extension) and not isinstance(backend_extension, BackendProtocol):
        raise TypeError(
            "Custom backend specs must resolve to a BackendProtocol instance, "
            "class, or backend factory"
        )
    return backend_extension


def route_skill_sources(
    backend: BackendProtocol | Any,
    skill_sources: tuple[SkillSourceConfig, ...],
) -> tuple[BackendProtocol | Any, tuple[str, ...]]:
    route_backends: dict[str, FilesystemBackend] = {}
    active_sources: list[str] = []

    for skill_source in skill_sources:
        source_path = _normalize_backend_path(skill_source.source_path, trailing_slash=True)
        disk_path = _materialize_selected_skill_source(skill_source)

        if not disk_path.exists():
            logger.warning(
                "Skipping skill source %s because %s does not exist",
                source_path,
                disk_path,
            )
            continue
        if not disk_path.is_dir():
            logger.warning(
                "Skipping skill source %s because %s is not a directory",
                source_path,
                disk_path,
            )
            continue

        skill_count = sum(1 for candidate in disk_path.glob("*/SKILL.md") if candidate.is_file())
        if skill_count == 0:
            logger.warning(
                "Skill source %s mapped to %s but no skill folders were found; "
                "expected <source>/<skill-name>/SKILL.md",
                source_path,
                disk_path,
            )
        else:
            logger.info(
                "Skill source %s mapped to %s with %d skill folder(s)",
                source_path,
                disk_path,
                skill_count,
            )

        if source_path in route_backends:
            logger.warning(
                "Duplicate skill source path %s detected; "
                "keeping the last configured disk directory",
                source_path,
            )
        route_backends[source_path] = FilesystemBackend(root_dir=disk_path, virtual_mode=True)
        if source_path not in active_sources:
            active_sources.append(source_path)

    if not route_backends:
        return backend, ()

    if isinstance(backend, SandboxBackendProtocol):
        routed_backend: BackendProtocol | Any = SkillRoutingSandboxBackend(
            primary_backend=backend,
            route_backends=route_backends,
        )
    else:
        routed_backend = SkillRoutingBackend(
            primary_backend=backend,
            route_backends=route_backends,
        )
    return routed_backend, tuple(active_sources)


def _flatten_loaded_specs(specs: list[str] | tuple[str, ...]) -> list[Any]:
    loaded: list[Any] = []
    for spec in specs:
        value = load_object_from_spec(spec)
        loaded.extend(flatten_components(value))
    return loaded


def _exports_from_module(module: ModuleType, names: tuple[str, ...]) -> list[Any]:
    exported: list[Any] = []
    for name in names:
        if hasattr(module, name):
            exported.extend(flatten_components(getattr(module, name)))
    return exported


def _directory_for_registry(path_or_file: str | Path) -> Path:
    path = Path(path_or_file)
    if path.is_file():
        return path.parent
    return path


class SelectablePathRegistry:
    def __init__(
        self,
        *,
        root: Path,
        ids: tuple[str, ...],
        source_prefix: str,
        markdown: bool = False,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.ids = ids
        self.source_prefix = source_prefix.rstrip("/")
        self.markdown = markdown

    @classmethod
    def from_directory(cls, root: Path, *, source_prefix: str) -> SelectablePathRegistry:
        resolved = root.expanduser().resolve()
        ids = tuple(
            sorted(
                candidate.name
                for candidate in resolved.iterdir()
                if candidate.is_dir() and (candidate / "SKILL.md").is_file()
            )
        )
        return cls(root=resolved, ids=ids, source_prefix=source_prefix)

    @classmethod
    def from_markdown_directory(cls, root: Path) -> SelectablePathRegistry:
        resolved = root.expanduser().resolve()
        ids = tuple(
            sorted(candidate.stem for candidate in resolved.glob("*.md") if candidate.is_file())
        )
        return cls(root=resolved, ids=ids, source_prefix="/memory", markdown=True)

    def select(
        self,
        ids: list[str] | tuple[str, ...],
    ) -> tuple[SkillSourceConfig, ...] | tuple[str, ...]:
        selected = tuple(ids)
        missing = sorted(set(selected) - set(self.ids))
        if missing:
            raise ValueError(f"Unknown registry item(s): {', '.join(missing)}")
        if self.markdown:
            return tuple(str(self.root / f"{item}.md") for item in selected)
        return (
            SkillSourceConfig(
                source_path=f"{self.source_prefix}/",
                disk_path=str(self.root),
                include=selected,
            ),
        )

    def all(self) -> tuple[SkillSourceConfig, ...] | tuple[str, ...]:
        return self.select(self.ids)


def _materialize_selected_skill_source(skill_source: SkillSourceConfig) -> Path:
    disk_path = Path(skill_source.disk_path).expanduser().resolve()
    if not skill_source.include:
        return disk_path

    digest = hashlib.sha256(
        "|".join([str(disk_path), *skill_source.include]).encode("utf-8")
    ).hexdigest()[:16]
    target = Path(tempfile.gettempdir()) / "open_deepagents_skill_sources" / digest
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    for skill_id in skill_source.include:
        source = disk_path / skill_id
        if not (source / "SKILL.md").is_file():
            raise ValueError(f"Selected skill {skill_id!r} does not contain SKILL.md")
        shutil.copytree(source, target / skill_id)
    return target


class BuiltinToolSelectionMiddleware(AgentMiddleware[Any, Any, Any]):
    def __init__(
        self,
        *,
        allowlist: frozenset[str] | None,
        blocklist: frozenset[str],
    ) -> None:
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
        if name not in DEEPAGENTS_BUILTIN_TOOLS:
            return True
        if self._allowlist is not None and name not in self._allowlist:
            return False
        return name not in self._blocklist


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        return str(tool.get("name") or "")
    return str(getattr(tool, "name", "") or getattr(tool, "__name__", "") or "")


class SkillRoutingBackend(BackendProtocol):
    def __init__(
        self,
        *,
        primary_backend: BackendProtocol,
        route_backends: Mapping[str, FilesystemBackend],
    ) -> None:
        self._primary_backend = primary_backend
        self._route_backends = {
            _normalize_backend_path(source_path, trailing_slash=True): backend
            for source_path, backend in route_backends.items()
        }
        self._ordered_route_paths = tuple(
            sorted(self._route_backends.keys(), key=len, reverse=True)
        )

    def ls(self, path: str) -> LsResult:
        matched = self._match_backend(path)
        if matched is None:
            return self._primary_backend.ls(path)
        route_path, backend_path, route_backend = matched
        result = route_backend.ls(backend_path)
        if result.entries is None:
            return result
        return LsResult(
            entries=[
                {
                    **entry,
                    "path": _restore_route_path(route_path, str(entry["path"])),
                }
                for entry in result.entries
            ],
            error=result.error,
        )

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        matched = self._match_backend(file_path)
        if matched is None:
            return self._primary_backend.read(file_path, offset=offset, limit=limit)
        _, backend_path, route_backend = matched
        return route_backend.read(backend_path, offset=offset, limit=limit)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        if path is None:
            return self._primary_backend.grep(pattern, path=path, glob=glob)
        matched = self._match_backend(path)
        if matched is None:
            return self._primary_backend.grep(pattern, path=path, glob=glob)
        route_path, backend_path, route_backend = matched
        result = route_backend.grep(pattern, path=backend_path, glob=glob)
        if result.matches is None:
            return result
        return GrepResult(
            matches=[
                {
                    **match,
                    "path": _restore_route_path(route_path, str(match["path"])),
                }
                for match in result.matches
            ],
            error=result.error,
        )

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        matched = self._match_backend(path)
        if matched is None:
            return self._primary_backend.glob(pattern, path=path)
        route_path, backend_path, route_backend = matched
        result = route_backend.glob(pattern, path=backend_path)
        if result.matches is None:
            return result
        return GlobResult(
            matches=[
                {
                    **match,
                    "path": _restore_route_path(route_path, str(match["path"])),
                }
                for match in result.matches
            ],
            error=result.error,
        )

    def write(self, file_path: str, content: str) -> WriteResult:
        matched = self._match_backend(file_path)
        if matched is None:
            return self._primary_backend.write(file_path, content)
        _, backend_path, route_backend = matched
        return route_backend.write(backend_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        matched = self._match_backend(file_path)
        if matched is None:
            return self._primary_backend.edit(
                file_path,
                old_string,
                new_string,
                replace_all=replace_all,
            )
        _, backend_path, route_backend = matched
        return route_backend.edit(
            backend_path,
            old_string,
            new_string,
            replace_all=replace_all,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for path, content in files:
            matched = self._match_backend(path)
            if matched is None:
                responses.extend(self._primary_backend.upload_files([(path, content)]))
                continue
            route_path, backend_path, route_backend = matched
            [response] = route_backend.upload_files([(backend_path, content)])
            response_path = getattr(response, "path", None)
            if isinstance(response_path, str):
                response.path = _restore_route_path(route_path, response_path)
            responses.append(response)
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            matched = self._match_backend(path)
            if matched is None:
                responses.extend(self._primary_backend.download_files([path]))
                continue
            _, backend_path, route_backend = matched
            responses.extend(route_backend.download_files([backend_path]))
        return responses

    def __getattr__(self, name: str) -> Any:
        return getattr(self._primary_backend, name)

    def _match_backend(
        self,
        path: str,
    ) -> tuple[str, str, FilesystemBackend] | None:
        normalized = _normalize_backend_path(path)
        for route_path in self._ordered_route_paths:
            route_prefix = route_path.rstrip("/")
            if normalized == route_prefix or normalized.startswith(f"{route_prefix}/"):
                suffix = normalized[len(route_prefix) :] or "/"
                if not suffix.startswith("/"):
                    suffix = f"/{suffix}"
                return route_path, suffix, self._route_backends[route_path]
        return None


class SkillRoutingSandboxBackend(SkillRoutingBackend, SandboxBackendProtocol):
    def __init__(
        self,
        *,
        primary_backend: SandboxBackendProtocol,
        route_backends: Mapping[str, FilesystemBackend],
    ) -> None:
        super().__init__(primary_backend=primary_backend, route_backends=route_backends)
        self._primary_sandbox_backend = primary_backend

    @property
    def id(self) -> str:
        return self._primary_sandbox_backend.id

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if timeout is None:
            return self._primary_sandbox_backend.execute(command)
        return self._primary_sandbox_backend.execute(command, timeout=timeout)


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


def _normalize_backend_path(path: str, *, trailing_slash: bool = False) -> str:
    normalized = path.replace("\\", "/")
    if not normalized.startswith("/"):
        normalized = f"/{normalized.lstrip('/')}"
    if trailing_slash and not normalized.endswith("/"):
        return f"{normalized}/"
    return normalized.rstrip("/") if normalized != "/" and not trailing_slash else normalized


def _restore_route_path(route_path: str, delegated_path: str) -> str:
    normalized = _normalize_backend_path(
        delegated_path,
        trailing_slash=delegated_path.endswith("/"),
    )
    if normalized == "/":
        return route_path
    return f"{route_path.rstrip('/')}{normalized}"
