from functools import lru_cache
from pathlib import Path

from langchain_openai import ChatOpenAI
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from deepagents_integration import DeepAgentsRuntimeConfig, SandboxConfig


class Settings(BaseSettings):
    app_name: str = "DeepAgents Agent Platform Backend"
    api_prefix: str = "/api"
    database_url: str | None = None
    mysql_host: str | None = None
    mysql_port: int = 3306
    mysql_database: str | None = None
    mysql_user: str | None = None
    mysql_password: str | None = None
    admin_email: str | None = None
    admin_username: str = "admin"
    admin_password: str = "change-me"
    admin_token_secret: str = "change-me-too"
    admin_token_expire_minutes: int = 720
    cors_allowed_origins: str | None = "http://127.0.0.1:5173,http://localhost:5173"
    upload_storage_dir: Path = Field(default=Path("./data/uploads"))
    upload_root: Path | None = None
    max_upload_size_bytes: int = 10 * 1024 * 1024
    deepagents_model: str | None = None
    deepagents_system_prompt: str = "You are the primary DeepAgents runtime for this web scaffold."
    deepagents_agent_name: str = "deepagents-web"
    deepagents_debug: bool = False
    deepagents_tool_specs: str | None = None
    deepagents_middleware_specs: str | None = None
    deepagents_skills: str | None = None
    deepagents_memory: str | None = None
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

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator(
        "database_url",
        "mysql_host",
        "mysql_database",
        "mysql_user",
        "mysql_password",
        "admin_email",
        "deepagents_model",
        "deepagents_sandbox_root_dir",
        "deepagents_sandbox_backend_spec",
        "custom_api_key",
        "custom_api_url",
        "custom_api_model",
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

    @model_validator(mode="after")
    def apply_runtime_defaults(self) -> "Settings":
        if self.database_url is None:
            if all([self.mysql_host, self.mysql_database, self.mysql_user, self.mysql_password]):
                self.database_url = (
                    f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
                    f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
                )
            else:
                self.database_url = "sqlite+pysqlite:///./data/backend.db"
        if self.upload_root is not None:
            self.upload_storage_dir = self.upload_root
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
        return DeepAgentsRuntimeConfig(
            model=self.resolve_model(),
            system_prompt=self.deepagents_system_prompt,
            agent_name=self.deepagents_agent_name,
            debug=self.deepagents_debug,
            tool_specs=self._split_csv(self.deepagents_tool_specs),
            middleware_specs=self._split_csv(self.deepagents_middleware_specs),
            skills=self._split_csv(self.deepagents_skills),
            memory=self._split_csv(self.deepagents_memory),
            permissions=tuple(),
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

    def resolve_model(self) -> str | ChatOpenAI | None:
        if self.custom_api_key and self.custom_api_url and self.custom_api_model:
            base_url = self.custom_api_url.rstrip("/")
            if base_url.endswith("/chat/completions"):
                base_url = base_url[: -len("/chat/completions")]
            elif not base_url.endswith("/v1"):
                base_url = f"{base_url}/v1"
            return ChatOpenAI(
                model=self.custom_api_model,
                api_key=SecretStr(self.custom_api_key),
                base_url=base_url,
                temperature=0,
            )
        return self.deepagents_model

    def get_cors_origins(self) -> list[str]:
        return list(self._split_csv(self.cors_allowed_origins))

    @staticmethod
    def _split_csv(value: str | None) -> tuple[str, ...]:
        if not value:
            return ()
        return tuple(item.strip() for item in value.split(",") if item.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
