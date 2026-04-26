import json
from collections.abc import Mapping
from datetime import datetime
from functools import lru_cache
from pathlib import Path, PurePath
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_openai import ChatOpenAI
from langgraph.cache.memory import InMemoryCache
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import DotEnvSettingsSource, EnvSettingsSource

from app.core.model_catalog import load_model_catalog
from app.core.runtime_catalog import RuntimeSelection, resolve_runtime, runtime_options
from deepagents_integration import DeepAgentsRuntimeConfig, SandboxConfig
from deepagents_integration.extensions import load_object_from_spec

BACKEND_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ENV_PATH = BACKEND_ROOT / ".env"
DEFAULT_AGENT_SYSTEM_PROMPT_PATH = BACKEND_ROOT / "agents" / "prompts" / "system.md"
DEFAULT_MODEL_EXAMPLE_PATH = BACKEND_ROOT / "models.example.json"
DEFAULT_SANDBOX_READ_PATHS = (
    (BACKEND_ROOT / "data").resolve(),
    (BACKEND_ROOT / "agents" / "skills").resolve(),
    (BACKEND_ROOT / "agents" / "memory").resolve(),
)
DEFAULT_SANDBOX_ROOT = (BACKEND_ROOT / "data").resolve()
_RUNTIME_TIMEZONE: ZoneInfo | None = None


class _LenientComplexEmptyMixin:
    def prepare_field_value(
        self,
        field_name: str,
        field: Any,
        value: Any,
        value_is_complex: bool,
    ) -> Any:
        if field_name in {"admin_users"} and value == "":
            value = "{}"
        return super().prepare_field_value(field_name, field, value, value_is_complex)  # type: ignore[misc]


class _LenientEnvSettingsSource(_LenientComplexEmptyMixin, EnvSettingsSource):
    pass


class _LenientDotEnvSettingsSource(_LenientComplexEmptyMixin, DotEnvSettingsSource):
    pass


class Settings(BaseSettings):
    app_name: str = "DeepAgents Agent Platform Backend"
    api_prefix: str = "/api"
    database_url: str | None = "sqlite+pysqlite:///./data/backend.db"
    admin_email: str | None = None
    admin_username: str = "admin"
    admin_password: str = "change-me"
    admin_users: dict[str, str] = Field(default_factory=dict)
    admin_token_secret: str = "change-me-too"
    admin_token_expire_minutes: int = 720
    admin_auth_enabled: bool = True
    cors_allowed_origins: str | None = "http://127.0.0.1:5173,http://localhost:5173"
    upload_storage_dir: Path = Field(default=Path("./data/uploads"))
    max_upload_size_bytes: int = 10 * 1024 * 1024
    deepagents_model_config_path: str | None = "./models.json"
    deepagents_main_agent: str = "agents:AGENT"
    deepagents_agent_name: str = "deepagents-web"
    deepagents_debug: bool = False
    deepagents_default_timezone: str = "Asia/Shanghai"
    deepagents_builtin_tools: str | None = None
    deepagents_disabled_builtin_tools: str | None = None
    deepagents_recursion_limit: int = 500
    deepagents_stream_idle_timeout: float = 180
    deepagents_sandbox_kind: str = "state"
    deepagents_sandbox_root_dir: str | None = None
    deepagents_sandbox_virtual_mode: bool | None = None
    deepagents_sandbox_timeout: int = 120
    deepagents_sandbox_max_output_bytes: int = 100_000
    deepagents_sandbox_inherit_env: bool = False
    deepagents_sandbox_backend_spec: str | None = None
    model_config = SettingsConfigDict(
        env_file=BACKEND_ENV_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
        enable_decoding=False,
        populate_by_name=True,
    )

    @field_validator(
        "database_url",
        "admin_email",
        "deepagents_model_config_path",
        "deepagents_main_agent",
        "deepagents_sandbox_root_dir",
        "deepagents_sandbox_backend_spec",
        mode="before",
    )
    @classmethod
    def empty_string_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("deepagents_sandbox_virtual_mode", mode="before")
    @classmethod
    def optional_bool_from_env(cls, value: object) -> object:
        if value in ("", None):
            return None
        return value

    @field_validator("deepagents_default_timezone", mode="after")
    @classmethod
    def validate_default_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {value}") from exc
        return value

    @field_validator("admin_users", mode="before")
    @classmethod
    def parse_admin_users(cls, value: object) -> dict[str, str]:
        if value in ("", None):
            return {}
        if isinstance(value, dict):
            if not all(
                isinstance(key, str) and isinstance(item, str) for key, item in value.items()
            ):
                raise ValueError("admin_users must use string usernames and passwords")
            return dict(value)
        if not isinstance(value, str):
            raise ValueError("admin_users must be a JSON object or USERNAME=PASSWORD pairs")

        text = value.strip()
        if not text:
            return {}
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError("admin_users JSON is invalid") from exc
            if not isinstance(parsed, dict) or not all(
                isinstance(key, str) and isinstance(item, str) for key, item in parsed.items()
            ):
                raise ValueError("admin_users JSON must be an object of strings")
            return dict(parsed)

        users: dict[str, str] = {}
        for raw_item in text.replace("\n", ",").split(","):
            item = raw_item.strip()
            if not item:
                continue
            username, separator, password = item.partition("=")
            if not separator or not username.strip():
                raise ValueError("admin_users must be JSON or comma-separated USERNAME=PASSWORD")
            users[username.strip()] = password.strip()
        return users

    @field_validator("upload_storage_dir", mode="after")
    @classmethod
    def resolve_upload_storage_dir(cls, value: Path) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        return path.resolve()

    @field_validator("deepagents_sandbox_root_dir", mode="after")
    @classmethod
    def resolve_sandbox_root_dir(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(resolve_runtime_disk_path(value, base_dir=BACKEND_ROOT))

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> tuple[Any, Any, Any, Any]:
        env_settings = _LenientEnvSettingsSource(settings_cls)
        dotenv_settings = _LenientDotEnvSettingsSource(
            settings_cls,
            env_file=BACKEND_ENV_PATH,
            env_file_encoding="utf-8",
        )
        return init_settings, env_settings, dotenv_settings, file_secret_settings

    @property
    def is_sqlite(self) -> bool:
        assert self.database_url is not None
        return self.database_url.startswith("sqlite")

    @property
    def is_mysql(self) -> bool:
        assert self.database_url is not None
        return self.database_url.startswith("mysql")

    @property
    def sqlite_file_path(self) -> Path | None:
        if not self.is_sqlite:
            return None
        marker = ":///"
        assert self.database_url is not None
        if marker not in self.database_url:
            return None
        raw_path = self.database_url.split(marker, maxsplit=1)[1]
        if raw_path == ":memory:":
            return None
        return Path(raw_path)

    def to_runtime_config(
        self,
        selection: RuntimeSelection | None = None,
    ) -> DeepAgentsRuntimeConfig:
        model_catalog = self.load_model_catalog_for_runtime(selection)
        runtime_resolution = self.resolve_runtime(selection=selection, model_catalog=model_catalog)
        agent_skill_sources = tuple(runtime_resolution.agent.get("skill_sources") or ())
        agent_tools = tuple(runtime_resolution.agent.get("tools") or ())
        agent_middleware = tuple(runtime_resolution.agent.get("middleware") or ())
        hooks = runtime_resolution.agent.get("hooks")
        hook_mapping = hooks if isinstance(hooks, dict) else {}
        agent_run_input_hooks = tuple(hook_mapping.get("run_input") or ())
        agent_upload_hooks = tuple(hook_mapping.get("upload") or ())
        agent_memory = tuple(str(item) for item in (runtime_resolution.agent.get("memory") or ()))
        selected_model = self.resolve_model(
            model_catalog=model_catalog,
            model_id=runtime_resolution.model_id,
        )
        sandbox_root_dir = self.resolved_sandbox_root_dir()
        agent_builtin_allowlist = _optional_string_tuple(
            runtime_resolution.agent.get("builtin_tool_allowlist")
        )
        env_builtin_allowlist = self._optional_csv(self.deepagents_builtin_tools)
        agent_builtin_blocklist = _string_tuple(
            runtime_resolution.agent.get("builtin_tool_blocklist")
        )
        env_builtin_blocklist = self._split_csv(self.deepagents_disabled_builtin_tools)
        if not runtime_resolution.subagents:
            agent_builtin_blocklist = _dedupe_tuple((*agent_builtin_blocklist, "task"))
        agent_permissions = _mapping_tuple(runtime_resolution.agent.get("permissions"))
        return DeepAgentsRuntimeConfig(
            model=selected_model,
            system_prompt=self.load_deepagents_system_prompt(runtime_resolution.agent),
            agent_name=self.deepagents_agent_name,
            debug=self.deepagents_debug,
            tools=agent_tools,
            middleware=agent_middleware,
            run_input_hooks=agent_run_input_hooks,
            upload_hooks=agent_upload_hooks,
            builtin_tool_allowlist=(
                env_builtin_allowlist
                if env_builtin_allowlist is not None
                else agent_builtin_allowlist
            ),
            builtin_tool_blocklist=_dedupe_tuple(
                (*agent_builtin_blocklist, *env_builtin_blocklist)
            ),
            skills=tuple(source.source_path for source in agent_skill_sources),
            skill_sources=agent_skill_sources,
            memory=agent_memory,
            permissions=(*self.default_permissions(), *agent_permissions),
            subagents=runtime_resolution.subagents,
            model_id=runtime_resolution.model_id,
            runtime_selection={"model_id": runtime_resolution.model_id or ""},
            checkpointer=self.resolve_agent_runtime_value(runtime_resolution.agent, "checkpointer"),
            store=self.resolve_agent_runtime_value(runtime_resolution.agent, "store"),
            interrupt_on=_mapping_or_none(runtime_resolution.agent.get("interrupt_on")),
            cache=self.resolve_agent_runtime_value(runtime_resolution.agent, "cache"),
            sandbox=SandboxConfig(
                kind=self.deepagents_sandbox_kind,  # type: ignore[arg-type]
                root_dir=sandbox_root_dir,
                virtual_mode=self.resolved_sandbox_virtual_mode(),
                timeout=self.deepagents_sandbox_timeout,
                max_output_bytes=self.deepagents_sandbox_max_output_bytes,
                inherit_env=self.deepagents_sandbox_inherit_env,
                backend_spec=self.deepagents_sandbox_backend_spec,
            ),
        )

    def load_deepagents_system_prompt(self, agent: Mapping[str, Any] | None = None) -> str:
        if agent is not None and agent.get("system_prompt_path"):
            return Path(agent["system_prompt_path"]).read_text(encoding="utf-8").strip()
        if DEFAULT_AGENT_SYSTEM_PROMPT_PATH.is_file():
            return DEFAULT_AGENT_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
        raise FileNotFoundError(
            f"Agent system prompt not found: {DEFAULT_AGENT_SYSTEM_PROMPT_PATH}"
        )

    def default_permissions(self) -> tuple[dict[str, object], ...]:
        paths: list[Path] = list(DEFAULT_SANDBOX_READ_PATHS)
        if not any(_path_contains(base_path, self.upload_storage_dir) for base_path in paths):
            paths.append(self.upload_storage_dir)
        return (
            {
                "operations": ["read"],
                "paths": [normalize_sandbox_permission_path(path) for path in paths],
            },
        )

    def resolved_sandbox_root_dir(self) -> str | None:
        if self.deepagents_sandbox_root_dir:
            return self.deepagents_sandbox_root_dir
        if self.deepagents_sandbox_kind in {"filesystem", "local_shell"}:
            return str(DEFAULT_SANDBOX_ROOT)
        return None

    def resolved_sandbox_virtual_mode(self) -> bool | None:
        if self.deepagents_sandbox_kind in {"filesystem", "local_shell"}:
            return True
        if self.deepagents_sandbox_virtual_mode is not None:
            return self.deepagents_sandbox_virtual_mode
        return None

    def model_config_path(self) -> Path | None:
        if not self.deepagents_model_config_path:
            return None
        return resolve_runtime_disk_path(self.deepagents_model_config_path, base_dir=BACKEND_ROOT)

    def load_model_catalog(self) -> Any:
        return load_model_catalog(
            self.model_config_path(),
            fallback_path=DEFAULT_MODEL_EXAMPLE_PATH,
        )

    def load_model_catalog_for_runtime(self, selection: RuntimeSelection | None = None) -> Any:
        _ = selection
        return self.load_model_catalog()

    def resolve_runtime(
        self,
        *,
        selection: RuntimeSelection | None = None,
        model_catalog: Any = None,
    ) -> Any:
        return resolve_runtime(
            agent_spec=self.deepagents_main_agent,
            model_catalog=model_catalog,
            default_model_id=None,
            selection=selection,
        )

    def runtime_options(self) -> dict[str, Any]:
        model_catalog = self.load_model_catalog_for_runtime()
        options = runtime_options(
            model_catalog=model_catalog,
            default_model_id=model_catalog.default_model_id if model_catalog is not None else None,
        )
        return options

    def resolve_model(
        self,
        *,
        model_catalog: Any = None,
        model_id: str | None = None,
    ) -> str | ChatOpenAI | None:
        if model_catalog is not None:
            return cast(
                ChatOpenAI,
                model_catalog.resolve(model_id),
            )
        return None

    def resolve_agent_runtime_value(
        self,
        agent: Mapping[str, Any],
        key: str,
    ) -> Any:
        if key in agent:
            agent_value = agent.get(key)
            if isinstance(agent_value, str):
                return resolve_native_runtime_value(key=key, value=agent_value)
            return agent_value
        return None

    def runtime_timezone(self) -> ZoneInfo:
        return ZoneInfo(self.deepagents_default_timezone)

    def current_time(self) -> datetime:
        return datetime.now(self.runtime_timezone())

    def get_cors_origins(self) -> list[str]:
        return list(self._split_csv(self.cors_allowed_origins))

    def logging_summary(self) -> dict[str, object]:
        return {
            "app_name": self.app_name,
            "cors_origin_count": len(self.get_cors_origins()),
            "database_backend": (
                "sqlite" if self.is_sqlite else "mysql" if self.is_mysql else "other"
            ),
            "deepagents_agent_name": self.deepagents_agent_name,
            "deepagents_model_configured": bool(self.load_model_catalog() is not None),
            "deepagents_stream_idle_timeout": self.deepagents_stream_idle_timeout,
            "deepagents_default_timezone": self.deepagents_default_timezone,
            "admin_auth_enabled": self.admin_auth_enabled,
            "sandbox_kind": self.deepagents_sandbox_kind,
            "sandbox_root_dir_configured": bool(self.deepagents_sandbox_root_dir),
            "sandbox_root_dir_effective": bool(self.resolved_sandbox_root_dir()),
            "upload_storage_dir": str(self.upload_storage_dir),
        }

    def runtime_model_logging_summary(self) -> dict[str, object]:
        model_catalog = self.load_model_catalog()
        model_source = "model_catalog" if model_catalog is not None else "unset"
        default_model_id = model_catalog.default_model_id if model_catalog is not None else None
        model_provider, model_name = describe_model_reference(default_model_id)

        return {
            "selected_model_source": model_source,
            "selected_model_provider": model_provider,
            "selected_model_name": model_name,
        }

    @staticmethod
    def _split_csv(value: str | None) -> tuple[str, ...]:
        if not value:
            return ()
        return tuple(item.strip() for item in value.split(",") if item.strip())

    @staticmethod
    def _optional_csv(value: str | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        return Settings._split_csv(value)


def normalize_sandbox_permission_path(path: str | Path | PurePath) -> str:
    """Return a DeepAgents-safe absolute path string across POSIX and Windows hosts."""

    normalized = path.as_posix() if isinstance(path, PurePath) else str(path).replace("\\", "/")
    if len(normalized) >= 3 and normalized[1] == ":" and normalized[2] == "/":
        return f"/{normalized}"
    return normalized


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


def _dedupe_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return tuple(deduped)


def _mapping_or_none(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("Expected a mapping")
    return value


def normalize_runtime_backend_path(
    path_value: str,
    *,
    base_dir: Path,
    trailing_slash: bool = False,
) -> str:
    disk_path = resolve_runtime_disk_path(path_value, base_dir=base_dir)
    if _path_contains(base_dir, disk_path):
        normalized = "/" + disk_path.relative_to(base_dir).as_posix().lstrip("/")
    else:
        normalized = normalize_sandbox_permission_path(disk_path)
    if trailing_slash and not normalized.endswith("/"):
        return f"{normalized}/"
    return normalized


def resolve_runtime_disk_path(path_value: str, *, base_dir: Path) -> Path:
    candidate = Path(path_value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (base_dir / candidate).resolve()


def _path_contains(base_path: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(base_path.resolve())
        return True
    except ValueError:
        return False


def describe_model_reference(model: str | None) -> tuple[str, str]:
    if not model:
        return "", ""
    provider, separator, name = model.partition("/")
    if separator:
        return provider or "string", name
    provider, separator, name = model.partition(":")
    if separator:
        return provider or "string", name
    return "string", model


def set_runtime_timezone(value: str) -> None:
    global _RUNTIME_TIMEZONE
    _RUNTIME_TIMEZONE = ZoneInfo(value)


def active_runtime_timezone() -> ZoneInfo:
    return _RUNTIME_TIMEZONE or get_settings().runtime_timezone()


def runtime_now() -> datetime:
    return datetime.now(active_runtime_timezone())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def resolve_native_runtime_value(*, key: str, value: str | None) -> Any:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or normalized.lower() == "none":
        return None
    if normalized.lower() == "memory":
        if key == "checkpointer":
            return InMemorySaver()
        if key == "store":
            return InMemoryStore()
        if key == "cache":
            return InMemoryCache()
    spec = normalized.removeprefix("custom:")
    return load_object_from_spec(spec)
