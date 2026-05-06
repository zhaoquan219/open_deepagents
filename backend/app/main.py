from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect

from app.auth import sync_configured_users
from app.catalog import load_model_catalog, resolve_agent, validate_model_catalog
from app.db import PRODUCT_TABLES, Database
from app.routes import router
from app.runtime.extensions import SandboxConfig
from app.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_settings.validate_startup()
    database = Database(resolved_settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved_settings.prepare_paths()
        database.initialize_schema()
        with database.session() as db:
            sync_configured_users(db, resolved_settings)
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
    app.include_router(router, prefix=resolved_settings.api_prefix)

    @app.get("/health", tags=["system"])
    def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["system"])
    def readiness() -> dict[str, object]:
        checks: dict[str, object] = {}
        with database.engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        checks["product_db"] = "ok"
        product_tables = set(inspect(database.engine).get_table_names())
        if product_tables != PRODUCT_TABLES:
            checks["schema"] = "invalid"
            return {
                "status": "error",
                "checks": checks,
                "expected_product_tables": sorted(PRODUCT_TABLES),
                "actual_tables": sorted(product_tables),
            }
        checks["schema"] = "ok"
        catalog = load_model_catalog(resolved_settings)
        validate_model_catalog(catalog, selected_model_id=None, production=False)
        checks["model_catalog"] = "ok"
        resolved_settings.assert_runtime_persistence_allowed()
        checks["runtime_persistence"] = resolved_settings.runtime_driver_mode()
        SandboxConfig.from_mapping(resolved_settings.sandbox_settings())
        checks["sandbox"] = "ok"
        resolve_agent(resolved_settings)
        checks["agent_package"] = "ok"
        return {"status": "ok", "checks": checks}

    return app


app = create_app()
