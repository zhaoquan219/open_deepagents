import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import inspect
from uvicorn.logging import DefaultFormatter

from app.auth import sync_configured_users
from app.catalog import load_model_catalog, resolve_agent, validate_model_catalog
from app.db import PRODUCT_TABLES, Database
from app.routes import router
from app.runtime.extensions import SandboxConfig
from app.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_backend_logging(resolved_settings)
    resolved_settings.validate_startup()
    database = Database(resolved_settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved_settings.prepare_paths()
        await resolved_settings.ainitialize_runtime_components()
        database.initialize_schema()
        with database.session() as db:
            sync_configured_users(db, resolved_settings)
        logging.info(
            "backend started checkpoint_backend=%s product_db=%s",
            resolved_settings.runtime_driver_mode(),
            resolved_settings.database_url,
        )
        yield
        await resolved_settings.aclose_runtime_components()
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
    app.include_router(router, prefix=resolved_settings.api_prefix)

    @app.get("/health", tags=["system"])
    def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["system"], response_model=None)
    async def readiness() -> Any:
        checks: dict[str, object] = {}
        with database.engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        checks["product_db"] = "ok"
        product_tables = set(inspect(database.engine).get_table_names())
        missing_tables = PRODUCT_TABLES - product_tables
        if missing_tables:
            checks["schema"] = "invalid"
            return JSONResponse(
                status_code=503,
                content={
                    "status": "error",
                    "checks": checks,
                    "expected_product_tables": sorted(PRODUCT_TABLES),
                    "actual_tables": sorted(product_tables),
                    "missing_product_tables": sorted(missing_tables),
                },
            )
        checks["schema"] = "ok"
        catalog = load_model_catalog(resolved_settings)
        validate_model_catalog(catalog, selected_model_id=None, production=False)
        checks["model_catalog"] = "ok"
        resolved_settings.assert_runtime_persistence_allowed()
        try:
            await _probe_runtime_persistence(resolved_settings)
        except Exception:
            logging.exception("runtime persistence readiness probe failed")
            checks["runtime_persistence"] = "error"
            checks["runtime_driver"] = resolved_settings.runtime_driver_mode()
            return JSONResponse(
                status_code=503,
                content={
                    "status": "error",
                    "checks": checks,
                    "error": "runtime persistence probe failed",
                },
            )
        checks["runtime_persistence"] = "ok"
        checks["runtime_driver"] = resolved_settings.runtime_driver_mode()
        SandboxConfig.from_mapping(resolved_settings.sandbox_settings())
        checks["sandbox"] = "ok"
        resolve_agent(resolved_settings)
        checks["agent_package"] = "ok"
        return {"status": "ok", "checks": checks}

    return app


def configure_backend_logging(settings: Settings) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(DefaultFormatter("%(levelprefix)s [%(name)s] %(message)s"))
    logging.basicConfig(level=settings.backend_log_levelno(), handlers=[handler])
    logging.getLogger("app").setLevel(settings.backend_log_levelno())


async def _probe_runtime_persistence(settings: Settings) -> None:
    checkpointer = settings.runtime_checkpointer()
    if checkpointer is None:
        raise RuntimeError("runtime checkpointer is not configured")
    config = {"configurable": {"thread_id": "__ready_probe__"}}
    await _call_runtime_probe(checkpointer, "aget_tuple", "get_tuple", config)

    store = settings.runtime_store()
    if store is None:
        raise RuntimeError("runtime store is not configured")
    await _call_runtime_probe(store, "aget", "get", ("__ready__",), "probe")


async def _call_runtime_probe(target: Any, async_name: str, sync_name: str, *args: Any) -> Any:
    async_method = getattr(target, async_name, None)
    if callable(async_method):
        return await async_method(*args)
    sync_method = getattr(target, sync_name, None)
    if callable(sync_method):
        return sync_method(*args)
    raise RuntimeError(f"{target.__class__.__name__} does not support readiness probes")


app = create_app()
