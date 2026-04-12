from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from deepagents_integration import DeepAgentsRuntimeConfig, SandboxConfig


class Settings(BaseSettings):
    app_name: str = "DeepAgents Agent Platform Backend"
    api_prefix: str = "/api"
    database_url: str = "sqlite+pysqlite:///./data/backend.db"
    admin_username: str = "admin"
    admin_password: str = "change-me"
    admin_token_secret: str = "change-me-too"
    admin_token_expire_minutes: int = 720
    upload_storage_dir: Path = Field(default=Path("./data/uploads"))
    max_upload_size_bytes: int = 10 * 1024 * 1024
    deepagents_model: str | None = None
    deepagents_system_prompt: str = "You are the primary DeepAgents runtime for this web scaffold."
    deepagents_agent_name: str = "deepagents-web"
    deepagents_debug: bool = False
    deepagents_tool_specs: str = ""
    deepagents_middleware_specs: str = ""
    deepagents_skills: str = ""
    deepagents_memory: str = ""
    deepagents_sandbox_kind: str = "state"
    deepagents_sandbox_root_dir: str | None = None
    deepagents_sandbox_virtual_mode: bool | None = None
    deepagents_sandbox_timeout: int = 120
    deepagents_sandbox_max_output_bytes: int = 100_000
    deepagents_sandbox_inherit_env: bool = False
    deepagents_sandbox_backend_spec: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sqlite_file_path(self) -> Path | None:
        if not self.is_sqlite:
            return None
        marker = ":///"
        if marker not in self.database_url:
            return None
        raw_path = self.database_url.split(marker, maxsplit=1)[1]
        if raw_path == ":memory:":
            return None
        return Path(raw_path)

    def to_runtime_config(self) -> DeepAgentsRuntimeConfig:
        return DeepAgentsRuntimeConfig(
            model=self.deepagents_model,
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

    @staticmethod
    def _split_csv(value: str) -> tuple[str, ...]:
        return tuple(item.strip() for item in value.split(",") if item.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
