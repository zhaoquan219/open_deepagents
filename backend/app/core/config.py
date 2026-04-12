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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
