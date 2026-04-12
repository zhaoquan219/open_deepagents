from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.database import DatabaseState
from app.services.runs import RunManager, RunService
from deepagents_integration import build_deep_agent


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    database = DatabaseState.from_settings(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if resolved_settings.sqlite_file_path is not None:
            resolved_settings.sqlite_file_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_settings.upload_storage_dir.mkdir(parents=True, exist_ok=True)
        database.create_all()
        yield
        database.dispose()

    app = FastAPI(
        title=resolved_settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.get_cors_origins() or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = resolved_settings
    app.state.database = database
    app.state.run_manager = RunManager()
    app.state.run_service = RunService(
        database=database,
        manager=app.state.run_manager,
        builder=build_deep_agent,
    )
    app.include_router(api_router, prefix=resolved_settings.api_prefix)

    @app.get("/health", tags=["system"])
    def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
