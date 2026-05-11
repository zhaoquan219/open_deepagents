from __future__ import annotations

import asyncio
import importlib
import json
import sys
from contextlib import AsyncExitStack, ExitStack
from functools import cached_property, lru_cache
from importlib import import_module
from importlib import util as importlib_util
from pathlib import Path
from typing import Any

from langgraph.cache.memory import InMemoryCache
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ADMIN_PASSWORD = "change-me"
DEFAULT_TOKEN_SECRET = "change-me-too"
DEFAULT_SANDBOX_ROOT = BACKEND_ROOT / "data" / "sandbox"
DEFAULT_UPLOAD_ROOT = BACKEND_ROOT / "data" / "uploads"
DEFAULT_RUNTIME_DB_URL = "sqlite+pysqlite:///./data/checkpoints.db"
LOG_LEVELS = frozenset({"critical", "error", "warning", "info", "debug"})


def import_from_spec(spec: str) -> Any:
    module_name, separator, attr = spec.partition(":")
    if not separator:
        raise ValueError(f"Import spec must be module:attribute: {spec}")
    if module_name.endswith(".py") or "/" in module_name:
        module = _import_file_module(module_name)
    else:
        module = importlib.import_module(module_name)
    value: Any = module
    for part in attr.split("."):
        value = getattr(value, part)
    return value


class Settings(BaseSettings):
    app_name: str = "DeepAgents Native Scaffold Backend"
    api_prefix: str = "/api"
    database_url: str = "sqlite+pysqlite:///./data/backend.db"
    environment: str = "development"
    cors_allowed_origins: str | None = "http://127.0.0.1:5173,http://localhost:5173"

    admin_username: str = "admin"
    admin_password: str = DEFAULT_ADMIN_PASSWORD
    admin_email: str | None = None
    admin_users: dict[str, str] = Field(default_factory=dict)
    admin_token_secret: str = DEFAULT_TOKEN_SECRET
    admin_token_expire_minutes: int = 720
    admin_auth_enabled: bool = True
    allow_anonymous_production: bool = False

    audit_compiled_prompts: str = "hash"
    allow_full_prompt_audit: bool = False
    deepagents_model_config_path: str | None = "./models.json"
    deepagents_main_agent: str = "agents:AGENT"
    deepagents_agent_name: str = "deepagents-web"
    deepagents_debug: bool = False
    deepagents_recursion_limit: int = 500
    backend_log_level: str = "info"

    deepagents_checkpoint_backend: str | None = None
    deepagents_checkpoint_database_url: str | None = None
    deepagents_runtime_driver: str | None = None
    deepagents_runtime_database_url: str | None = None
    deepagents_runtime_factory: str | None = None
    deepagents_backend_spec: str | None = None
    deepagents_sandbox_profile: str | None = None
    deepagents_sandbox_kind: str = "state"
    deepagents_sandbox_root_dir: str | None = None
    deepagents_upload_root_dir: str | None = None
    deepagents_sandbox_virtual_mode: bool | None = None
    deepagents_sandbox_timeout: int = 120
    deepagents_sandbox_max_output_bytes: int = 100_000
    deepagents_sandbox_inherit_env: bool = False
    deepagents_sandbox_env: dict[str, str] = Field(default_factory=dict)
    deepagents_sandbox_allow_shell: bool = False
    deepagents_sandbox_allow_env_inherit: bool = False
    deepagents_interrupt_on: dict[str, bool] = Field(default_factory=dict)

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        enable_decoding=False,
    )

    @field_validator(
        "cors_allowed_origins",
        "deepagents_model_config_path",
        "deepagents_main_agent",
        "deepagents_checkpoint_backend",
        "deepagents_checkpoint_database_url",
        "deepagents_runtime_driver",
        "deepagents_runtime_database_url",
        "deepagents_runtime_factory",
        "deepagents_backend_spec",
        "deepagents_sandbox_profile",
        "deepagents_sandbox_root_dir",
        "deepagents_upload_root_dir",
        mode="before",
    )
    @classmethod
    def none_if_empty(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("deepagents_sandbox_virtual_mode", mode="before")
    @classmethod
    def bool_or_none(cls, value: object) -> object:
        return None if value in ("", None) else value

    @field_validator("backend_log_level", mode="before")
    @classmethod
    def normalize_backend_log_level(cls, value: object) -> str:
        level = str(value or "info").strip().lower()
        if level not in LOG_LEVELS:
            raise ValueError(
                "BACKEND_LOG_LEVEL must be one of: critical, error, warning, info, debug"
            )
        return level

    @field_validator(
        "admin_users", "deepagents_interrupt_on", "deepagents_sandbox_env", mode="before"
    )
    @classmethod
    def parse_mapping(cls, value: object) -> object:
        if value in ("", None):
            return {}
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return {}
        if text.startswith("{"):
            return json.loads(text)
        return {
            key.strip(): val.strip()
            for raw in text.replace("\n", ",").split(",")
            if raw.strip()
            for key, _, val in [raw.partition("=")]
        }

    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    def get_cors_origins(self) -> list[str]:
        return [
            item.strip() for item in (self.cors_allowed_origins or "").split(",") if item.strip()
        ]

    def prepare_paths(self) -> None:
        DEFAULT_SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
        self.upload_root_dir().mkdir(parents=True, exist_ok=True)
        if self.runtime_driver_mode() == "sqlite":
            _ensure_sqlite_parent(self.runtime_database_url_for_builtin("sqlite"))
        _ensure_sqlite_parent(self.database_url)

    def configured_users(self) -> dict[str, str]:
        return {self.admin_username: self.admin_password, **self.admin_users}

    def runtime_driver_mode(self) -> str:
        mode = (
            self.deepagents_checkpoint_backend
            or self.deepagents_runtime_driver
            or "sqlite"
        ).strip().lower()
        if mode == "postgresql":
            mode = "postgres"
        if mode not in {"memory", "sqlite", "postgres", "factory"}:
            raise ValueError(
                "DEEPAGENTS_CHECKPOINT_BACKEND must be memory, sqlite, postgresql, "
                "or factory"
            )
        return mode

    def backend_log_levelno(self) -> int:
        import logging

        return int(getattr(logging, self.backend_log_level.upper()))

    @cached_property
    def runtime_components(self) -> dict[str, Any]:
        mode = self.runtime_driver_mode()
        if mode == "memory":
            return {
                "checkpointer": InMemorySaver(),
                "store": InMemoryStore(),
                "cache": InMemoryCache(),
            }
        if mode == "factory":
            return self._factory_runtime_components()
        return _create_runtime_components(
            mode=mode,
            database_url=self.runtime_database_url_for_builtin(mode),
        )

    async def ainitialize_runtime_components(self) -> None:
        if "runtime_components" in self.__dict__:
            return
        mode = self.runtime_driver_mode()
        if mode in {"memory", "factory"}:
            self.__dict__["runtime_components"] = self.runtime_components
            return
        self.__dict__["runtime_components"] = await _acreate_runtime_components(
            mode=mode,
            database_url=self.runtime_database_url_for_builtin(mode),
        )

    def _factory_runtime_components(self) -> dict[str, Any]:
        if not self.deepagents_runtime_factory:
            raise RuntimeError(
                "DEEPAGENTS_RUNTIME_DRIVER=factory requires DEEPAGENTS_RUNTIME_FACTORY."
            )
        runtime = import_from_spec(self.deepagents_runtime_factory)()
        if isinstance(runtime, dict):
            return runtime
        return {
            "checkpointer": getattr(runtime, "checkpointer", None),
            "store": getattr(runtime, "store", None),
            "cache": getattr(runtime, "cache", None),
        }

    def runtime_checkpointer(self) -> Any:
        return self.runtime_components.get("checkpointer")

    def runtime_store(self) -> Any:
        return self.runtime_components.get("store")

    def runtime_cache(self) -> Any:
        return self.runtime_components.get("cache")

    def close_runtime_components(self) -> None:
        components = self.__dict__.pop("runtime_components", None)
        if not isinstance(components, dict):
            return
        stack = components.get("_exit_stack")
        if isinstance(stack, ExitStack):
            stack.close()

    async def aclose_runtime_components(self) -> None:
        components = self.__dict__.pop("runtime_components", None)
        if not isinstance(components, dict):
            return
        async_stack = components.get("_async_exit_stack")
        if isinstance(async_stack, AsyncExitStack):
            await async_stack.aclose()
        stack = components.get("_exit_stack")
        if isinstance(stack, ExitStack):
            stack.close()

    def sandbox_settings(self) -> dict[str, Any]:
        default_root_dir = str(DEFAULT_SANDBOX_ROOT.resolve())
        configured_root_dir = self.deepagents_sandbox_root_dir
        if configured_root_dir and not Path(configured_root_dir).is_absolute():
            configured_root_dir = str((BACKEND_ROOT / configured_root_dir).resolve())
        config: dict[str, Any] = {
            "kind": self.deepagents_sandbox_kind,
            "root_dir": configured_root_dir,
            "skills_root_dir": str((BACKEND_ROOT / "agents" / "skills").resolve()),
            "uploads_root_dir": str(self.upload_root_dir().resolve()),
            "virtual_mode": self.deepagents_sandbox_virtual_mode,
            "timeout": self.deepagents_sandbox_timeout,
            "max_output_bytes": self.deepagents_sandbox_max_output_bytes,
            "inherit_env": self.deepagents_sandbox_inherit_env,
            "env": self.deepagents_sandbox_env,
            "backend_spec": self.deepagents_backend_spec,
        }
        profile = (self.deepagents_sandbox_profile or "").strip().lower()
        if not profile:
            return config
        if profile == "safe":
            return {**config, "kind": "state"}
        if profile in {"files", "shell"}:
            return {
                **config,
                "kind": "local_shell" if profile == "shell" else "filesystem",
                "root_dir": config["root_dir"] or default_root_dir,
                "virtual_mode": True if config["virtual_mode"] is None else config["virtual_mode"],
            }
        if profile == "custom":
            return {**config, "kind": "custom"}
        raise ValueError("DEEPAGENTS_SANDBOX_PROFILE must be one of: safe, files, shell, custom")

    def upload_root_dir(self) -> Path:
        configured = self.deepagents_upload_root_dir
        root = Path(configured) if configured else DEFAULT_UPLOAD_ROOT
        return root if root.is_absolute() else BACKEND_ROOT / root

    def validate_startup(self) -> None:
        self.assert_runtime_persistence_allowed()
        self.assert_production_guardrails()
        self.assert_model_catalog_safe()

    def runtime_database_url_for_builtin(self, mode: str) -> str:
        if mode not in {"sqlite", "postgres"}:
            raise RuntimeError(
                "Only sqlite/postgresql checkpoint modes use "
                "DEEPAGENTS_CHECKPOINT_DATABASE_URL."
            )
        configured_url = (
            self.deepagents_checkpoint_database_url
            or self.deepagents_runtime_database_url
        )
        if not configured_url and mode == "sqlite":
            configured_url = DEFAULT_RUNTIME_DB_URL
        if not configured_url:
            raise RuntimeError(
                f"DEEPAGENTS_CHECKPOINT_BACKEND={mode} requires "
                "DEEPAGENTS_CHECKPOINT_DATABASE_URL "
                "so runtime checkpoint/store tables cannot be created in the product database."
            )
        expected = "postgresql" if mode == "postgres" else mode
        if _driver_family(configured_url) != expected:
            raise RuntimeError(
                f"DEEPAGENTS_CHECKPOINT_BACKEND={mode} requires a {expected} "
                "DEEPAGENTS_CHECKPOINT_DATABASE_URL."
            )
        if _database_target(configured_url) == _database_target(
            self.database_url
        ):
            raise RuntimeError(
                "DEEPAGENTS_CHECKPOINT_DATABASE_URL must be different from DATABASE_URL; "
                "product DB schema is exactly users, sessions, runs, and events."
            )
        return configured_url

    def assert_runtime_persistence_allowed(self) -> None:
        mode = self.runtime_driver_mode()
        if mode in {"sqlite", "postgres"}:
            self.runtime_database_url_for_builtin(mode)
        if not self.is_production():
            return
        if mode == "memory":
            raise RuntimeError("Production cannot use in-memory runtime persistence.")
        if mode in {"sqlite", "postgres"}:
            return
        components = self.runtime_components
        if not components.get("checkpointer") or not components.get("store"):
            raise RuntimeError("Production requires durable LangGraph checkpointer and store.")
        if _is_memory_runtime(components.get("checkpointer")) or _is_memory_runtime(
            components.get("store")
        ):
            raise RuntimeError("Production cannot use in-memory runtime persistence.")
        if self.database_url.startswith("mysql") and not self.deepagents_runtime_factory:
            raise RuntimeError(
                "MySQL product DB in production requires explicit DEEPAGENTS_RUNTIME_FACTORY."
            )

    def assert_production_guardrails(self) -> None:
        if self.audit_compiled_prompts not in {"hash", "redacted", "full", "off"}:
            raise RuntimeError("AUDIT_COMPILED_PROMPTS must be one of: hash, redacted, full, off.")
        if not self.is_production():
            return
        checks = [
            (
                len(self.admin_token_secret) < 32
                or self.admin_token_secret == DEFAULT_TOKEN_SECRET,
                "Production requires ADMIN_TOKEN_SECRET with at least 32 characters.",
            ),
            (
                self.admin_password == DEFAULT_ADMIN_PASSWORD,
                "Production requires changing ADMIN_PASSWORD.",
            ),
            (
                not self.admin_auth_enabled and not self.allow_anonymous_production,
                "Production rejects ADMIN_AUTH_ENABLED=false unless "
                "ALLOW_ANONYMOUS_PRODUCTION=true.",
            ),
            (
                not self.get_cors_origins() or "*" in self.get_cors_origins(),
                "Production rejects wildcard CORS origins with credentials.",
            ),
            (
                self.sandbox_settings().get("kind") == "local_shell"
                and not self.deepagents_sandbox_allow_shell,
                "Production rejects shell sandbox unless DEEPAGENTS_SANDBOX_ALLOW_SHELL=true.",
            ),
            (
                self.sandbox_settings().get("inherit_env")
                and not self.deepagents_sandbox_allow_env_inherit,
                "Production rejects inherited shell env unless "
                "DEEPAGENTS_SANDBOX_ALLOW_ENV_INHERIT=true.",
            ),
            (
                self.audit_compiled_prompts == "full" and not self.allow_full_prompt_audit,
                "Production rejects AUDIT_COMPILED_PROMPTS=full unless "
                "ALLOW_FULL_PROMPT_AUDIT=true.",
            ),
        ]
        for failed, message in checks:
            if failed:
                raise RuntimeError(message)

    def assert_model_catalog_safe(self) -> None:
        from app.catalog import load_model_catalog, validate_model_catalog

        validate_model_catalog(
            load_model_catalog(self),
            selected_model_id=None,
            production=self.is_production(),
        )


def _import_file_module(module_name: str) -> Any:
    path = Path(module_name)
    if not path.is_absolute():
        path = BACKEND_ROOT / path
    module_spec = importlib_util.spec_from_file_location(
        f"_deepagents_spec_{abs(hash(path))}",
        path,
    )
    if module_spec is None or module_spec.loader is None:
        raise ValueError(f"Cannot import {module_name}")
    module = importlib_util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = module
    module_spec.loader.exec_module(module)
    return module


def _driver_family(database_url: str) -> str:
    raw = str(make_url(database_url).drivername).split("+", maxsplit=1)[0].lower()
    return {"postgres": "postgresql"}.get(raw, raw)


def _database_target(database_url: str) -> tuple[object, ...]:
    url = make_url(database_url)
    family = _driver_family(database_url)
    if family == "sqlite":
        database = url.database or ""
        if database in {"", ":memory:"}:
            return ("sqlite", database)
        raw_path = Path(database)
        path = raw_path if raw_path.is_absolute() else BACKEND_ROOT / raw_path
        return ("sqlite", str(path.resolve()))
    host = (url.host or "").lower().rstrip(".")
    host = {"127.0.0.1": "localhost", "::1": "localhost", "[::1]": "localhost"}.get(host, host)
    port = url.port or {"postgresql": 5432, "postgres": 5432, "mysql": 3306, "mariadb": 3306}.get(
        family, 0
    )
    return (family, host, port, url.database or "")


def _ensure_sqlite_parent(database_url: str) -> None:
    path = _sqlite_database_path(database_url)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)


def _sqlite_database_path(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite") or ":///" not in database_url:
        return None
    raw_path = database_url.split(":///", 1)[1]
    if raw_path == ":memory:":
        return None
    path = Path(raw_path)
    return path if path.is_absolute() else BACKEND_ROOT / path


def _sqlite_connection_string(database_url: str) -> str:
    if ":///" not in database_url:
        return database_url
    path = _sqlite_database_path(database_url)
    if path is None:
        return ":memory:"
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _create_runtime_components(*, mode: str, database_url: str) -> dict[str, Any]:
    specs = {
        "sqlite": (
            "langgraph.checkpoint.sqlite",
            "SqliteSaver",
            "langgraph.store.sqlite",
            "SqliteStore",
            _sqlite_connection_string(database_url),
        ),
        "postgres": (
            "langgraph.checkpoint.postgres",
            "PostgresSaver",
            "langgraph.store.postgres",
            "PostgresStore",
            database_url,
        ),
    }
    checkpoint_module, checkpoint_class, store_module, store_class, connection = specs[mode]
    try:
        checkpoint = import_module(checkpoint_module)
        store = import_module(store_module)
    except ImportError as exc:
        raise RuntimeError(
            f"DEEPAGENTS_CHECKPOINT_BACKEND={mode} requires official LangGraph "
            f"checkpoint/store packages (langgraph-checkpoint-{mode}) or "
            "DEEPAGENTS_RUNTIME_FACTORY."
        ) from exc
    stack = ExitStack()
    checkpointer = stack.enter_context(
        getattr(checkpoint, checkpoint_class).from_conn_string(connection)
    )
    runtime_store = stack.enter_context(getattr(store, store_class).from_conn_string(connection))
    for component in (checkpointer, runtime_store):
        setup = getattr(component, "setup", None)
        if callable(setup):
            setup()
    return {
        "checkpointer": checkpointer,
        "store": runtime_store,
        "cache": None,
        "_exit_stack": stack,
    }


async def _acreate_runtime_components(*, mode: str, database_url: str) -> dict[str, Any]:
    specs = {
        "sqlite": (
            "langgraph.checkpoint.sqlite.aio",
            "AsyncSqliteSaver",
            "langgraph.store.sqlite.aio",
            "AsyncSqliteStore",
            _sqlite_connection_string(database_url),
        ),
        "postgres": (
            "langgraph.checkpoint.postgres.aio",
            "AsyncPostgresSaver",
            "langgraph.store.postgres.aio",
            "AsyncPostgresStore",
            database_url,
        ),
    }
    checkpoint_module, checkpoint_class, store_module, store_class, connection = specs[mode]
    try:
        checkpoint = import_module(checkpoint_module)
        store = import_module(store_module)
    except ImportError as exc:
        raise RuntimeError(
            f"DEEPAGENTS_CHECKPOINT_BACKEND={mode} requires async LangGraph "
            f"checkpoint/store packages (langgraph-checkpoint-{mode}) or "
            "DEEPAGENTS_RUNTIME_FACTORY."
        ) from exc
    stack = AsyncExitStack()
    checkpointer = await stack.enter_async_context(
        getattr(checkpoint, checkpoint_class).from_conn_string(connection)
    )
    runtime_store = await stack.enter_async_context(
        getattr(store, store_class).from_conn_string(connection)
    )
    for component in (checkpointer, runtime_store):
        setup = getattr(component, "setup", None)
        if callable(setup):
            result = setup()
            if asyncio.iscoroutine(result):
                await result
    return {
        "checkpointer": checkpointer,
        "store": runtime_store,
        "cache": None,
        "_async_exit_stack": stack,
    }


def _is_memory_runtime(value: Any) -> bool:
    return value.__class__.__name__ in {"InMemorySaver", "InMemoryStore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
