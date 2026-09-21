from __future__ import annotations

from pathlib import Path
from typing import Any

from app.catalog import build_model
from app.settings import Settings, get_settings

__all__ = ("AppConfig", "config")


class AppConfig:
    """Unified runtime configuration entrypoint for the agents package.

    Agents depend on this single, stable surface instead of reaching into app
    internals directly. Add a property here when a new configuration value
    needs to be exposed to agents.
    """

    @property
    def settings(self) -> Settings:
        return get_settings()

    def model(self, model_id: str | None = None) -> Any:
        return build_model(self.settings, model_id)

    @property
    def upload_dir(self) -> Path:
        return self.settings.upload_root_dir()

    @property
    def sandbox_root_dir(self) -> str | None:
        return self.settings.sandbox_settings().get("root_dir")


config = AppConfig()
