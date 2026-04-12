from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
