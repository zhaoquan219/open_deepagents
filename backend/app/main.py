import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import Settings, get_settings, set_runtime_timezone
from app.core.database import DatabaseState
from app.core.logging import format_log_message
from app.services.runs import RunManager, RunService
from deepagents_integration import build_deep_agent

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    resolved_settings = settings or get_settings()
    set_runtime_timezone(resolved_settings.deepagents_default_timezone)
    database = DatabaseState.from_settings(resolved_settings)
    startup_summary = resolved_settings.logging_summary()
    logger.info(
        "%s",
        format_log_message(
            f"application config loaded for {startup_summary['database_backend']} backend",
            event="app.config_loaded",
            phase="startup",
            **startup_summary,
        ),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if resolved_settings.sqlite_file_path is not None:
            resolved_settings.sqlite_file_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_settings.upload_storage_dir.mkdir(parents=True, exist_ok=True)
        database.initialize_schema()
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
    app.state.prompt_injections = app.state.run_service.prompt_injections
    app.include_router(api_router, prefix=resolved_settings.api_prefix)

    @app.get("/health", tags=["system"])
    def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
