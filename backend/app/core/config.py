import json
from functools import lru_cache
from pathlib import Path, PurePath
from urllib.parse import urlsplit, urlunsplit

from langchain_openai import ChatOpenAI
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from deepagents_integration import DeepAgentsRuntimeConfig, SandboxConfig, SkillSourceConfig

BACKEND_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ENV_PATH = BACKEND_ROOT / ".env"
DEEPAGENTS_SYSTEM_PROMPT_PATH = BACKEND_ROOT / "prompts" / "deepagents-system-prompt.md"
DEFAULT_SANDBOX_READ_PATHS = (
    (BACKEND_ROOT / "data").resolve(),
    (BACKEND_ROOT / "extensions" / "skills").resolve(),
)


class Settings(BaseSettings):
    app_name: str = "DeepAgents Agent Platform Backend"
    api_prefix: str = "/api"
    database_url: str | None = None
    admin_email: str | None = None
    admin_username: str = "admin"
    admin_password: str = "change-me"
    admin_token_secret: str = "change-me-too"
    admin_token_expire_minutes: int = 720
    cors_allowed_origins: str | None = "http://127.0.0.1:5173,http://localhost:5173"
    upload_storage_dir: Path = Field(default=Path("./data/uploads"))
    max_upload_size_bytes: int = 10 * 1024 * 1024
    deepagents_model: str | None = None
    deepagents_agent_name: str = "deepagents-web"
    deepagents_debug: bool = False
    deepagents_tool_specs: str | None = None
    deepagents_middleware_specs: str | None = None
    deepagents_skills: str | None = None
    deepagents_memory: str | None = None
    deepagents_recursion_limit: int = 60
    deepagents_sandbox_kind: str = "state"
    deepagents_sandbox_root_dir: str | None = None
    deepagents_sandbox_virtual_mode: bool | None = None
    deepagents_sandbox_timeout: int = 120
    deepagents_sandbox_max_output_bytes: int = 100_000
    deepagents_sandbox_inherit_env: bool = False
    deepagents_sandbox_backend_spec: str | None = None
    custom_api_key: str | None = None
    custom_api_url: str | None = None
    custom_api_model: str | None = None
    custom_api_temperature: float | None = None
    custom_api_default_headers: dict[str, str] = Field(default_factory=dict)

    model_config = SettingsConfigDict(
        env_file=BACKEND_ENV_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator(
        "database_url",
        "admin_email",
        "deepagents_model",
        "deepagents_sandbox_root_dir",
        "deepagents_sandbox_backend_spec",
        "custom_api_key",
        "custom_api_url",
        "custom_api_model",
        "custom_api_temperature",
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

    @field_validator("custom_api_default_headers", mode="before")
    @classmethod
    def parse_custom_api_default_headers(cls, value: object) -> dict[str, str]:
        if value in ("", None):
            return {}
        if isinstance(value, dict):
            if not all(
                isinstance(key, str) and isinstance(item, str) for key, item in value.items()
            ):
                raise ValueError("custom_api_default_headers must use string keys and values")
            return dict(value)
        if not isinstance(value, str):
            raise ValueError("custom_api_default_headers must be a JSON object or KEY=VALUE pairs")

        text = value.strip()
        if not text:
            return {}
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError("custom_api_default_headers JSON is invalid") from exc
            if not isinstance(parsed, dict) or not all(
                isinstance(key, str) and isinstance(item, str) for key, item in parsed.items()
            ):
                raise ValueError("custom_api_default_headers JSON must be an object of strings")
            return dict(parsed)

        headers: dict[str, str] = {}
        for raw_item in text.replace("\n", ",").split(","):
            item = raw_item.strip()
            if not item:
                continue
            key, separator, header_value = item.partition("=")
            if not separator or not key.strip():
                raise ValueError(
                    "custom_api_default_headers must be JSON or comma-separated KEY=VALUE pairs"
                )
            headers[key.strip()] = header_value.strip()
        return headers

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

    @model_validator(mode="after")
    def apply_runtime_defaults(self) -> "Settings":
        if self.database_url is None:
            self.database_url = "sqlite+pysqlite:///./data/backend.db"
        if self.deepagents_model is None and self.custom_api_model is not None:
            self.deepagents_model = self.custom_api_model
        return self

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

    def to_runtime_config(self) -> DeepAgentsRuntimeConfig:
        resolved_skill_sources = self.deepagents_skill_sources()
        return DeepAgentsRuntimeConfig(
            model=self.resolve_model(),
            system_prompt=self.load_deepagents_system_prompt(),
            agent_name=self.deepagents_agent_name,
            debug=self.deepagents_debug,
            tool_specs=self._split_csv(self.deepagents_tool_specs),
            middleware_specs=self._split_csv(self.deepagents_middleware_specs),
            skills=tuple(source.source_path for source in resolved_skill_sources),
            skill_sources=resolved_skill_sources,
            memory=self._split_csv(self.deepagents_memory),
            permissions=self.default_permissions(),
            sandbox=SandboxConfig(
                kind=self.deepagents_sandbox_kind,  # type: ignore[arg-type]
                root_dir=self.deepagents_sandbox_root_dir,
                virtual_mode=self.deepagents_sandbox_virtual_mode,
                timeout=self.deepagents_sandbox_timeout,
                max_output_bytes=self.deepagents_sandbox_max_output_bytes,
                inherit_env=self.deepagents_sandbox_inherit_env,
                backend_spec=self.deepagents_sandbox_backend_spec,
            ),
        )

    def load_deepagents_system_prompt(self) -> str:
        return DEEPAGENTS_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()

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

    def resolve_model(self) -> str | ChatOpenAI | None:
        if self.custom_api_key and self.custom_api_url and self.custom_api_model:
            base_url = self.custom_api_url.rstrip("/")
            if base_url.endswith("/chat/completions"):
                base_url = base_url[: -len("/chat/completions")]
            elif not base_url.endswith("/v1"):
                base_url = f"{base_url}/v1"
            api_key = SecretStr(self.custom_api_key)
            if self.custom_api_temperature is not None and self.custom_api_default_headers:
                return ChatOpenAI(
                    model=self.custom_api_model,
                    api_key=api_key,
                    base_url=base_url,
                    temperature=self.custom_api_temperature,
                    default_headers=self.custom_api_default_headers,
                )
            if self.custom_api_temperature is not None:
                return ChatOpenAI(
                    model=self.custom_api_model,
                    api_key=api_key,
                    base_url=base_url,
                    temperature=self.custom_api_temperature,
                )
            if self.custom_api_default_headers:
                return ChatOpenAI(
                    model=self.custom_api_model,
                    api_key=api_key,
                    base_url=base_url,
                    default_headers=self.custom_api_default_headers,
                )
            return ChatOpenAI(
                model=self.custom_api_model,
                api_key=api_key,
                base_url=base_url,
            )
        return self.deepagents_model

    def deepagents_skill_sources(
        self,
        *,
        base_dir: Path = BACKEND_ROOT,
    ) -> tuple[SkillSourceConfig, ...]:
        sources: list[SkillSourceConfig] = []
        for raw_source in self._split_csv(self.deepagents_skills):
            disk_path = resolve_runtime_disk_path(raw_source, base_dir=base_dir)
            source_path = normalize_runtime_backend_path(
                raw_source,
                base_dir=base_dir,
                trailing_slash=True,
            )
            sources.append(
                SkillSourceConfig(
                    source_path=source_path,
                    disk_path=str(disk_path),
                )
            )
        return tuple(sources)

    def get_cors_origins(self) -> list[str]:
        return list(self._split_csv(self.cors_allowed_origins))

    def logging_summary(self) -> dict[str, object]:
        return {
            "app_name": self.app_name,
            "cors_origin_count": len(self.get_cors_origins()),
            "custom_api_enabled": bool(
                self.custom_api_key and self.custom_api_url and self.custom_api_model
            ),
            "database_backend": (
                "sqlite" if self.is_sqlite else "mysql" if self.is_mysql else "other"
            ),
            "deepagents_agent_name": self.deepagents_agent_name,
            "deepagents_model_configured": bool(self.deepagents_model or self.custom_api_model),
            "sandbox_kind": self.deepagents_sandbox_kind,
            "upload_storage_dir": str(self.upload_storage_dir),
        }

    def runtime_model_logging_summary(self) -> dict[str, object]:
        model_source = "unset"
        model_provider = ""
        model_name = ""
        custom_model_base_url: str | None = None
        custom_model_headers_count = 0
        custom_model_temperature_configured = False

        if self._uses_custom_api_model():
            model_source = "custom_api"
            model_provider = "custom_api"
            model_name = self.custom_api_model or ""
            custom_model_base_url = self.normalized_custom_api_base_url()
            custom_model_headers_count = len(self.custom_api_default_headers)
            custom_model_temperature_configured = self.custom_api_temperature is not None
        elif self.deepagents_model:
            model_source = "configured_model"
            model_provider, model_name = describe_model_reference(self.deepagents_model)

        return {
            "selected_model_source": model_source,
            "selected_model_provider": model_provider,
            "selected_model_name": model_name,
            "custom_model_base_url": custom_model_base_url,
            "custom_model_headers_count": custom_model_headers_count,
            "custom_model_temperature_configured": custom_model_temperature_configured,
        }

    def normalized_custom_api_base_url(self) -> str | None:
        if not self.custom_api_url:
            return None
        parts = urlsplit(self.custom_api_url)
        path = parts.path.rstrip("/")
        if path.endswith("/chat/completions"):
            path = path[: -len("/chat/completions")]
        elif not path.endswith("/v1"):
            path = f"{path}/v1"
        hostname = parts.hostname or parts.netloc
        if not hostname:
            return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
        netloc = hostname
        if parts.port is not None:
            netloc = f"{hostname}:{parts.port}"
        return urlunsplit((parts.scheme, netloc, path, "", ""))

    def _uses_custom_api_model(self) -> bool:
        return bool(self.custom_api_key and self.custom_api_url and self.custom_api_model)

    @staticmethod
    def _split_csv(value: str | None) -> tuple[str, ...]:
        if not value:
            return ()
        return tuple(item.strip() for item in value.split(",") if item.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def normalize_sandbox_permission_path(path: str | Path | PurePath) -> str:
    """Return a DeepAgents-safe absolute path string across POSIX and Windows hosts."""

    normalized = path.as_posix() if isinstance(path, PurePath) else str(path).replace("\\", "/")
    if len(normalized) >= 3 and normalized[1] == ":" and normalized[2] == "/":
        return f"/{normalized}"
    return normalized


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
    provider, separator, name = model.partition(":")
    if separator:
        return provider or "string", name
    return "string", model
