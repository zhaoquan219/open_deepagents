from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.database import DatabaseState


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    database = DatabaseState.from_settings(resolved_settings)
    database.create_all()
    resolved_settings.upload_storage_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title=resolved_settings.app_name, version="0.1.0")
    app.state.settings = resolved_settings
    app.state.database = database
    app.include_router(api_router, prefix=resolved_settings.api_prefix)

    @app.get("/health", tags=["system"])
    def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @app.on_event("shutdown")
    def shutdown() -> None:
        database.dispose()

    return app


app = create_app()
