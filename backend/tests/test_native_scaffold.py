from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import warnings
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from deepagents.middleware.skills import _list_skills
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from agents.middleware.audit_middleware import inject_attachment_context_message
from app.agent import DeepAgentsRunContext, build_deep_agent
from app.auth import hash_password
from app.catalog import (
    _path as catalog_path,
)
from app.catalog import (
    build_model,
    load_model_catalog,
    model_options,
    resolve_agent,
    resolve_permissions,
    validate_model_catalog,
)
from app.db import (
    Database,
    EventRecord,
    RunRecord,
    SessionRecord,
    UploadRecord,
    UserRecord,
    append_event,
    now,
)
from app.main import create_app
from app.path_utils import is_import_spec, split_import_spec
from app.routes import _archive_session, _runtime_input_messages
from app.runtime.extensions import SandboxConfig, load_object_from_spec, resolve_backend
from app.settings import Settings, _database_target, _sqlite_database_path, import_from_spec


def login_headers(
    client: TestClient,
    username: str = "admin",
    password: str = "secret",
) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def sse_events(text: str) -> list[dict[str, Any]]:
    events = []
    for block in text.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


class FakeGraph:
    def __init__(self) -> None:
        self.configs: list[Any] = []
        self.contexts: list[Any] = []
        self.inputs: list[Any] = []

    async def astream_events(
        self,
        agent_input: Any,
        *,
        config: Any,
        context: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        self.inputs.append(agent_input)
        self.configs.append(config)
        self.contexts.append(context)
        yield {
            "event": "on_chain_start",
            "name": "agent",
            "data": {"input": {"messages": [HumanMessage(content="hello")]}},
        }
        yield {"event": "on_tool_start", "name": "echo", "data": {"input": {"text": "hello"}}}
        yield {"event": "on_tool_end", "name": "echo", "data": {"output": {"text": "echo:hello"}}}
        yield {
            "event": "on_tool_start",
            "name": "execute",
            "data": {"input": {"command": "python -c 'print(1)'"}},
        }
        yield {
            "event": "on_tool_end",
            "name": "execute",
            "data": {"output": {"stdout": "1\n", "stderr": ""}},
        }
        yield {
            "event": "on_chat_model_stream",
            "name": "model",
            "data": {"chunk": {"content": "Hi"}},
        }
        yield {
            "event": "on_chat_model_end",
            "name": "model",
            "data": {"output": AIMessage(content="Hi")},
        }
        yield {
            "event": "on_chain_end",
            "name": "deepagents-web",
            "data": {"output": {"messages": [AIMessage(content="Hi")]}},
        }


class ChainEndOnlyGraph:
    async def astream_events(
        self,
        agent_input: Any,
        *,
        config: Any,
        context: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        del agent_input, config, context
        yield {
            "event": "on_chain_end",
            "name": "deepagents-web",
            "data": {"output": {"messages": [AIMessage(content="Simple final")]}},
        }


class NestedCompletionGraph:
    async def astream_events(
        self,
        agent_input: Any,
        *,
        config: Any,
        context: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        del agent_input, config, context
        yield {
            "event": "on_chain_end",
            "name": "SkillsMiddleware.before_agent",
            "data": {"output": {"skills_metadata": []}},
        }
        yield {
            "event": "on_chat_model_end",
            "name": "model",
            "metadata": {"langgraph_node": "model"},
            "data": {"output": AIMessage(content="Real final")},
        }
        yield {
            "event": "on_chain_end",
            "name": "deepagents-web",
            "data": {"output": {"messages": [AIMessage(content="Real final")]}},
        }


class ToolInterleavedModelGraph:
    async def astream_events(
        self,
        agent_input: Any,
        *,
        config: Any,
        context: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        del agent_input, config, context
        yield {
            "event": "on_chat_model_end",
            "name": "model",
            "data": {"output": AIMessage(content="准备调用工具")},
        }
        yield {
            "event": "on_tool_start",
            "name": "execute",
            "data": {"input": {"command": "echo ok"}},
        }
        yield {
            "event": "on_tool_end",
            "name": "execute",
            "data": {"output": "ok\n[Command succeeded with exit code 0]"},
        }
        yield {
            "event": "on_chat_model_end",
            "name": "model",
            "data": {"output": AIMessage(content="工具完成")},
        }
        yield {
            "event": "on_chain_end",
            "name": "deepagents-web",
            "data": {"output": {"messages": [AIMessage(content="工具完成")]}},
        }


class FailingCheckpoint:
    async def aget_tuple(self, config: dict[str, Any]) -> Any:
        del config
        raise RuntimeError("checkpoint database unavailable")


def test_schema_is_product_projection(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    inspector = inspect(client.app.state.database.engine)
    assert set(inspector.get_table_names()) == {"events", "runs", "sessions", "uploads", "users"}
    indexes = {
        table: {index["name"] for index in inspector.get_indexes(table)}
        for table in ("users", "sessions", "runs", "events", "uploads")
    }
    assert "ix_users_email" in indexes["users"]
    assert "ix_sessions_owner_updated" in indexes["sessions"]
    assert "ix_runs_session_id" in indexes["runs"]
    assert "ix_runs_status" in indexes["runs"]
    assert "ix_events_run_id" in indexes["events"]
    assert "ix_events_kind" in indexes["events"]
    assert "ix_events_created_at" in indexes["events"]
    assert "ix_uploads_session_id" in indexes["uploads"]
    assert "ix_uploads_user_id" in indexes["uploads"]

    created = client.post("/api/sessions", headers=auth_headers, json={"title": "Demo"})
    assert created.status_code == 201
    session = created.json()
    assert session["thread_id"].startswith("thread-")
    assert session["owner_user_id"]

    with client.app.state.database.session() as db:
        record = db.get(SessionRecord, session["id"])
        assert record is not None
        assert record.owner.username == "admin"
        try:
            append_event(
                db,
                session_id=record.id,
                kind="bad.payload",
                type="step",
                payload={"not_json": object()},
            )
        except TypeError:
            pass
        else:
            raise AssertionError("non-serializable event payload was accepted")


def test_readiness_reports_missing_product_tables_without_repairing(
    client: TestClient,
) -> None:
    with client.app.state.database.engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE events")

    response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["checks"]["schema"] == "invalid"
    assert body["missing_product_tables"] == ["events"]
    inspector = inspect(client.app.state.database.engine)
    assert "events" not in set(inspector.get_table_names())


def test_auth_sync_uses_sql_user_state(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'auth.db'}",
        admin_username="admin",
        admin_password="secret",
        admin_email="admin@example.com",
        admin_users={"alice": "one"},
        admin_token_secret="test-secret-key-with-32-bytes-minimum",
        deepagents_model_config_path=str(tmp_path / "models.json"),
    )
    (tmp_path / "models.json").write_text(
        '{"model":"test/fake","provider":{"test":{"models":{"fake":{"model":"fake"}}}}}',
        encoding="utf-8",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert login_headers(client, "alice", "one")

    with app.state.database.session() as db:
        user = db.scalar(select(UserRecord).where(UserRecord.username == "alice"))
        assert user is not None
        user.is_active = False

    app = create_app(Settings(**{**settings.model_dump(), "admin_users": {"alice": "two"}}))
    with TestClient(app) as client:
        response = client.post("/api/auth/login", json={"username": "alice", "password": "two"})
        assert response.status_code == 401


def test_sessions_are_owner_isolated(client: TestClient, auth_headers: dict[str, str]) -> None:
    session = client.post("/api/sessions", headers=auth_headers, json={"title": "Private"}).json()
    with client.app.state.database.session() as db:
        db.add(UserRecord(username="bob", password_hash=hash_password("secret")))
    bob = login_headers(client, "bob", "secret")
    assert client.get("/api/sessions", headers=bob).json() == []
    assert client.get(f"/api/sessions/{session['id']}", headers=bob).status_code == 404


def test_query_string_tokens_are_rejected(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    token = auth_headers["Authorization"].split(" ", 1)[1]

    response = client.get(f"/api/sessions?access_token={token}")

    assert response.status_code == 401


def test_delete_archives_session_and_revokes_history_and_uploads(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    session = client.post("/api/sessions", headers=auth_headers, json={"title": "Delete me"}).json()
    upload = client.post(
        f"/api/sessions/{session['id']}/uploads",
        headers=auth_headers,
        files={"file": ("notes.txt", b"secret upload", "text/plain")},
    ).json()
    assert (
        client.get(f"/api/uploads/{upload['id']}/content", headers=auth_headers).status_code
        == 200
    )

    delete = client.delete(f"/api/sessions/{session['id']}", headers=auth_headers)

    assert delete.status_code == 204
    assert all(
        row["id"] != session["id"]
        for row in client.get("/api/sessions", headers=auth_headers).json()
    )
    assert client.get(f"/api/sessions/{session['id']}", headers=auth_headers).status_code == 404
    assert (
        client.get(f"/api/sessions/{session['id']}/events", headers=auth_headers).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/sessions/{session['id']}/runs",
            headers=auth_headers,
            json={"prompt": "should not run"},
        ).status_code
        == 404
    )
    assert (
        client.get(f"/api/uploads/{upload['id']}/content", headers=auth_headers).status_code
        == 404
    )
    with client.app.state.database.session() as db:
        session_record = db.get(SessionRecord, session["id"])
        upload_record = db.get(UploadRecord, upload["id"])
        assert session_record is not None
        assert session_record.status == "archived"
        assert session_record.archived_at is not None
        assert upload_record is not None
        assert upload_record.status == "deleted"
        assert upload_record.deleted_at is not None


def test_models_catalog_public_output_and_validation(tmp_path: Path, monkeypatch) -> None:
    catalog = tmp_path / "models.json"
    catalog.write_text(
        json.dumps(
            {
                "model": "openai/gpt",
                "provider": {
                    "openai": {
                        "name": "OpenAI",
                        "options": {
                            "api_key": "${OPENAI_API_KEY}",
                            "base_url": "https://example.test/v1",
                        },
                        "models": {"gpt": {"name": "GPT", "model": "gpt-test"}},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    settings = Settings(deepagents_model_config_path=str(catalog))
    payload = model_options(settings)
    assert payload["default_model_id"] == "openai/gpt"
    encoded = json.dumps(payload)
    assert "secret" not in encoded
    assert "OPENAI_API_KEY" not in encoded

    try:
        build_model(settings, "openai/unknown")
    except ValueError as exc:
        assert "Unknown selected model" in str(exc) or "Unknown model selection" in str(exc)
    else:
        raise AssertionError("unknown model id was accepted")


def test_models_catalog_supports_repo_models_json_shape() -> None:
    settings = Settings(
        deepagents_model_config_path=str(Path(__file__).resolve().parents[1] / "models.json")
    )
    catalog = load_model_catalog(settings)
    validate_model_catalog(catalog, selected_model_id=None, production=False)

    options = model_options(settings)
    assert options["default_model_id"] == catalog["model"]
    assert any(model["id"] == catalog["model"] for model in options["models"])


def test_models_catalog_accepts_chat_openai_fields_and_warns_on_extras(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = tmp_path / "models.json"
    catalog.write_text(
        json.dumps(
            {
                "model": "openai/gpt",
                "provider": {
                    "openai": {
                        "name": "OpenAI",
                        "options": {
                            "api_key": "${OPENAI_API_KEY}",
                            "base_url": "https://example.test/v1",
                            "default_query": {"api-version": "2026-01-01"},
                            "unknown_provider_knob": True,
                        },
                        "models": {
                            "gpt": {
                                "name": "Display name",
                                "model": "gpt-test",
                                "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
                                "model_kwargs": {"custom": "value"},
                                "verbosity": "low",
                                "use_responses_api": True,
                                "unknown_model_knob": 123,
                            }
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = Settings(deepagents_model_config_path=str(catalog))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        validate_model_catalog(
            load_model_catalog(settings),
            selected_model_id=None,
            production=False,
        )
        model = build_model(settings)

    warning_text = "\n".join(str(item.message) for item in caught)
    assert "unknown_provider_knob" in warning_text
    assert "unknown_model_knob" in warning_text
    assert model.model_name == "gpt-test"
    assert str(model.openai_api_base) == "https://example.test/v1"
    assert model.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
    assert model.model_kwargs == {"custom": "value"}
    assert model.verbosity == "low"
    assert model.use_responses_api is True


def test_windows_drive_import_specs_split_on_attribute_colon(monkeypatch) -> None:
    loaded_modules: list[str] = []

    def fake_import_file(module_name: str) -> Any:
        loaded_modules.append(module_name)
        return SimpleNamespace(AGENT={"id": "main"}, build_backend=lambda: "backend")

    monkeypatch.setattr("app.settings._import_file_module", fake_import_file)
    monkeypatch.setattr("app.runtime.extensions._import_module_or_file", fake_import_file)

    assert split_import_spec(r"D:\agents\agent.py:AGENT") == (
        r"D:\agents\agent.py",
        "AGENT",
    )
    assert not is_import_spec(r"D:\agents\agent.py")
    assert import_from_spec(r"D:\agents\agent.py:AGENT") == {"id": "main"}
    assert load_object_from_spec(r"D:\runtime\backend.py:build_backend")() == "backend"
    assert loaded_modules == [r"D:\agents\agent.py", r"D:\runtime\backend.py"]


def test_windows_drive_paths_from_env_are_not_rebased_to_backend_root() -> None:
    assert str(catalog_path(r"D:\models\models.json")) == r"D:\models\models.json"

    settings = Settings(
        deepagents_sandbox_profile="files",
        deepagents_sandbox_root_dir=r"D:\deepagents\sandbox",
        deepagents_upload_root_dir=r"D:\deepagents\uploads",
    )

    sandbox = settings.sandbox_settings()
    assert sandbox["root_dir"] == r"D:\deepagents\sandbox"
    assert sandbox["uploads_root_dir"] == r"D:\deepagents\uploads"
    assert str(settings.upload_root_dir()) == r"D:\deepagents\uploads"
    assert str(_sqlite_database_path("sqlite:///D:/deepagents/checkpoints.db")) == (
        "D:/deepagents/checkpoints.db"
    )
    assert _database_target("sqlite:///D:/deepagents/backend.db") == (
        "sqlite",
        "D:/deepagents/backend.db",
    )


def test_empty_model_config_env_disables_catalog_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPAGENTS_MODEL_CONFIG_PATH", "")

    settings = Settings()

    assert settings.deepagents_model_config_path is None
    settings.validate_startup()


def test_product_database_url_accepts_sqlite_postgres_and_mysql(tmp_path: Path) -> None:
    urls = (
        f"sqlite+pysqlite:///{tmp_path / 'product.db'}",
        "postgresql+psycopg://app:pw@localhost:5432/open_deepagents",
        "mysql+pymysql://app:pw@localhost:3306/open_deepagents?charset=utf8mb4",
    )

    for url in urls:
        assert Settings(database_url=url).database_url == url


def test_settings_guardrails_reject_unsafe_production() -> None:
    durable = {"checkpointer": object(), "store": object(), "cache": object()}
    cases: list[tuple[Settings, str, str]] = [
        (
            Settings(environment="production", deepagents_runtime_driver="memory"),
            "runtime",
            "in-memory runtime",
        ),
        (
            Settings(
                environment="production",
                deepagents_runtime_driver="factory",
                deepagents_runtime_factory="langgraph.checkpoint.memory:InMemorySaver",
            ),
            "runtime",
            "store",
        ),
        (
            Settings(environment="production", admin_token_secret="x" * 40),
            "guardrail",
            "ADMIN_PASSWORD",
        ),
        (
            Settings(
                environment="production",
                admin_token_secret="x" * 40,
                admin_password="secret",
                admin_auth_enabled=False,
            ),
            "guardrail",
            "ADMIN_AUTH_ENABLED=false",
        ),
        (
            Settings(
                environment="production",
                admin_token_secret="x" * 40,
                admin_password="secret",
                cors_allowed_origins="*",
            ),
            "guardrail",
            "wildcard CORS",
        ),
        (
            Settings(
                environment="production",
                admin_token_secret="x" * 40,
                admin_password="secret",
                deepagents_sandbox_profile="shell",
            ),
            "guardrail",
            "shell sandbox",
        ),
        (
            Settings(
                environment="production",
                admin_token_secret="x" * 40,
                admin_password="secret",
                audit_compiled_prompts="full",
            ),
            "guardrail",
            "AUDIT_COMPILED_PROMPTS=full",
        ),
    ]
    for settings, check, message in cases:
        if check == "guardrail":
            settings.__dict__["runtime_components"] = durable
            settings.deepagents_runtime_driver = "factory"
        try:
            if check == "runtime":
                settings.assert_runtime_persistence_allowed()
            else:
                settings.assert_production_guardrails()
        except (RuntimeError, TypeError) as exc:
            assert message in str(exc)
        else:
            raise AssertionError(f"production guardrail accepted unsafe case: {message}")


def test_runtime_resolver_checkpoint_backends_and_database_isolation(tmp_path: Path) -> None:
    default = Settings(deepagents_model_config_path="")
    assert default.runtime_driver_mode() == "sqlite"
    assert default.runtime_database_url_for_builtin("sqlite").endswith("/data/checkpoints.db")

    sqlite = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'product.db'}",
        deepagents_checkpoint_backend="sqlite",
        deepagents_checkpoint_database_url=f"sqlite+pysqlite:///{tmp_path / 'runtime.db'}",
    )
    assert sqlite.runtime_checkpointer().__class__.__name__ == "SqliteSaver"
    assert sqlite.runtime_store().__class__.__name__ == "SqliteStore"
    sqlite.close_runtime_components()

    postgresql = Settings(
        deepagents_checkpoint_backend="postgresql",
        deepagents_checkpoint_database_url=(
            "postgresql+psycopg://app:pw@localhost:5432/open_deepagents_checkpoints"
        ),
    )
    assert postgresql.runtime_driver_mode() == "postgres"
    assert "open_deepagents_checkpoints" in postgresql.runtime_database_url_for_builtin("postgres")

    memory = Settings(deepagents_checkpoint_backend="memory")
    assert memory.runtime_checkpointer().__class__.__name__ == "InMemorySaver"
    assert memory.runtime_store().__class__.__name__ == "InMemoryStore"

    module = tmp_path / "runtime_bundle.py"
    module.write_text(
        "def build_runtime():\n"
        "    return {'checkpointer': object(), 'store': object(), 'cache': object()}\n",
        encoding="utf-8",
    )
    factory = Settings(
        deepagents_runtime_driver="factory",
        deepagents_runtime_factory=f"{module}:build_runtime",
    )
    assert factory.runtime_components["checkpointer"] is factory.runtime_checkpointer()
    assert factory.runtime_components["store"] is factory.runtime_store()

    shared = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'product.db'}",
        deepagents_checkpoint_backend="sqlite",
        deepagents_checkpoint_database_url=f"sqlite+pysqlite:///{tmp_path / 'product.db'}",
    )
    try:
        _ = shared.runtime_components
    except RuntimeError as exc:
        assert "must be different from DATABASE_URL" in str(exc)
    else:
        raise AssertionError("official sqlite runtime accepted shared product database URL")

    equivalent_shared = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'shared.db'}",
        deepagents_checkpoint_backend="sqlite",
        deepagents_checkpoint_database_url=f"sqlite:///{tmp_path / 'shared.db'}",
    )
    try:
        _ = equivalent_shared.runtime_components
    except RuntimeError as exc:
        assert "must be different from DATABASE_URL" in str(exc)
    else:
        raise AssertionError("official sqlite runtime accepted equivalent product DB URL")

    server_shared = Settings(
        database_url=(
            "postgresql+psycopg://app:pw@127.0.0.1:5432/open_deepagents"
            "?sslmode=require"
        ),
        deepagents_checkpoint_backend="postgresql",
        deepagents_checkpoint_database_url=(
            "postgresql+psycopg://other:pw@127.0.0.1:5432/open_deepagents"
        ),
    )
    try:
        _ = server_shared.runtime_components
    except RuntimeError as exc:
        assert "must be different from DATABASE_URL" in str(exc)
    else:
        raise AssertionError("official runtime accepted equivalent server database URL")

    default_port_shared = Settings(
        database_url="postgresql+psycopg://app:pw@127.0.0.1/open_deepagents",
        deepagents_checkpoint_backend="postgresql",
        deepagents_checkpoint_database_url=(
            "postgresql+psycopg://other:pw@127.0.0.1:5432/open_deepagents"
        ),
    )
    try:
        _ = default_port_shared.runtime_components
    except RuntimeError as exc:
        assert "must be different from DATABASE_URL" in str(exc)
    else:
        raise AssertionError("official runtime accepted default-port equivalent DB URL")

    host_case_shared = Settings(
        database_url="postgres://app:pw@LOCALHOST/open_deepagents",
        deepagents_checkpoint_backend="postgresql",
        deepagents_checkpoint_database_url=(
            "postgresql+psycopg://other:pw@localhost:5432/open_deepagents"
        ),
    )
    try:
        _ = host_case_shared.runtime_components
    except RuntimeError as exc:
        assert "must be different from DATABASE_URL" in str(exc)
    else:
        raise AssertionError("official runtime accepted host-case equivalent DB URL")

    localhost_alias_shared = Settings(
        database_url="postgresql+psycopg://app:pw@127.0.0.1/open_deepagents",
        deepagents_checkpoint_backend="postgresql",
        deepagents_checkpoint_database_url=(
            "postgresql+psycopg://other:pw@localhost:5432/open_deepagents"
        ),
    )
    try:
        _ = localhost_alias_shared.runtime_components
    except RuntimeError as exc:
        assert "must be different from DATABASE_URL" in str(exc)
    else:
        raise AssertionError("official runtime accepted localhost alias equivalent DB URL")

    wrong_family = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'product.db'}",
        deepagents_checkpoint_backend="postgresql",
        deepagents_checkpoint_database_url=f"sqlite+pysqlite:///{tmp_path / 'runtime.db'}",
    )
    try:
        _ = wrong_family.runtime_components
    except RuntimeError as exc:
        assert "requires a postgresql" in str(exc)
    else:
        raise AssertionError("postgres runtime accepted non-postgres runtime DB URL")

    try:
        Settings(backend_log_level="verbose")
    except ValueError as exc:
        assert "BACKEND_LOG_LEVEL" in str(exc)
    else:
        raise AssertionError("invalid backend log level was accepted")


def test_app_lifespan_initializes_default_sqlite_runtime_as_async(tmp_path: Path) -> None:
    models_path = tmp_path / "models.json"
    models_path.write_text(
        '{"model":"test/fake","provider":{"test":{"models":{"fake":{"model":"fake"}}}}}',
        encoding="utf-8",
    )
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'product.db'}",
        deepagents_checkpoint_database_url=f"sqlite+pysqlite:///{tmp_path / 'checkpoints.db'}",
        deepagents_model_config_path=str(models_path),
    )

    with TestClient(create_app(settings)):
        assert settings.runtime_checkpointer().__class__.__name__ == "AsyncSqliteSaver"
        assert settings.runtime_store().__class__.__name__ == "AsyncSqliteStore"


def test_agent_resolution_supports_native_package_contract(tmp_path: Path) -> None:
    module = tmp_path / "agent_pkg.py"
    prompt = tmp_path / "system.md"
    memory = tmp_path / "memory.md"
    skill = tmp_path / "skill"
    skill.mkdir()
    prompt.write_text("main prompt", encoding="utf-8")
    memory.write_text("memory", encoding="utf-8")
    module.write_text(
        "\n".join(
            [
                "def echo(text: str) -> str: return text",
                "AGENT = {",
                " 'id': 'main', 'system_prompt': r'%s', 'tools': [echo],",
                " 'skills': [r'%s'], 'memory': [r'%s'], 'model': 'test/fake',",
                " 'permissions': [{'operations':['read'], 'paths':['/tmp']}],",
                " 'subagents': [{'id':'reviewer','name':'reviewer',",
                " 'description':'Review','system_prompt':'child'}],",
                "}",
            ]
        )
        % (prompt, skill, memory),
        encoding="utf-8",
    )
    resolved = resolve_agent(Settings(deepagents_main_agent=f"{module}:AGENT"))
    assert resolved["system_prompt"] == "main prompt"
    assert resolved["tools"][0]("x") == "x"
    assert resolved["skills"] == [str(skill)]
    assert resolved["memory"] == [str(memory)]
    assert resolved["model"] == "test/fake"
    assert resolved["permissions"][0].paths == ["/tmp", "/tmp/**"]
    assert resolved["subagents"][0]["name"] == "reviewer"


def test_agent_resolution_rejects_builtin_tool_schema(tmp_path: Path) -> None:
    module = tmp_path / "legacy_agent.py"
    module.write_text(
        "AGENT = {'id': 'legacy', 'builtin_tools': ['read_file'], "
        "'permissions': [{'operations': ['read'], 'paths': ['/tmp']}]}\n",
        encoding="utf-8",
    )

    try:
        resolve_agent(Settings(deepagents_main_agent=f"{module}:AGENT"))
    except ValueError as exc:
        assert "builtin_tools" in str(exc)
    else:
        raise AssertionError("legacy builtin_tools schema was accepted")

    module.write_text(
        "AGENT = {'id': 'legacy', "
        "'permissions': [{'builtin_tools': ['read_file'], 'paths': ['/tmp']}]}\n",
        encoding="utf-8",
    )
    try:
        resolve_agent(Settings(deepagents_main_agent=f"{module}:AGENT"))
    except ValueError as exc:
        assert "permissions[].builtin_tools" in str(exc)
    else:
        raise AssertionError("permissions[].builtin_tools schema was accepted")


def test_agent_resolution_expands_root_wildcards_for_components(tmp_path: Path) -> None:
    package = tmp_path / "agents_pkg"
    for folder in (
        "tools",
        "middleware",
        "skills/alpha",
        "memory",
        "subagents/reviewer/tools",
        "subagents/reviewer/middleware",
        "subagents/reviewer/skills/beta",
        "subagents/reviewer/memory",
    ):
        (package / folder).mkdir(parents=True, exist_ok=True)
    for init in (
        package / "__init__.py",
        package / "tools" / "__init__.py",
        package / "middleware" / "__init__.py",
        package / "skills" / "__init__.py",
        package / "memory" / "__init__.py",
        package / "subagents" / "__init__.py",
        package / "subagents" / "reviewer" / "__init__.py",
        package / "subagents" / "reviewer" / "tools" / "__init__.py",
        package / "subagents" / "reviewer" / "middleware" / "__init__.py",
        package / "subagents" / "reviewer" / "skills" / "__init__.py",
        package / "subagents" / "reviewer" / "memory" / "__init__.py",
    ):
        init.write_text("", encoding="utf-8")
    (package / "skills" / "alpha" / "SKILL.md").write_text("skill", encoding="utf-8")
    (package / "memory" / "project.md").write_text("memory", encoding="utf-8")
    (package / "tools" / "helper.py").write_text("VALUE = 'ok'\n", encoding="utf-8")
    (package / "tools" / "echo.py").write_text(
        "from .helper import VALUE\n\ndef echo(text: str) -> str: return f'{VALUE}:{text}'\n"
        "TOOLS = [echo]\n",
        encoding="utf-8",
    )
    (package / "middleware" / "marker.py").write_text("MIDDLEWARE = ['mw']\n", encoding="utf-8")
    (package / "subagents" / "reviewer" / "skills" / "beta" / "SKILL.md").write_text(
        "skill",
        encoding="utf-8",
    )
    (package / "subagents" / "reviewer" / "memory" / "review.md").write_text(
        "memory",
        encoding="utf-8",
    )
    (package / "subagents" / "reviewer" / "tools" / "helper.py").write_text(
        "PREFIX = 'child'\n",
        encoding="utf-8",
    )
    (package / "subagents" / "reviewer" / "tools" / "summarize.py").write_text(
        "from .helper import PREFIX\n\n"
        "def summarize(text: str) -> str: return f'{PREFIX}:{text.upper()}'\n"
        "TOOLS = [summarize]\n",
        encoding="utf-8",
    )
    (package / "subagents" / "reviewer" / "middleware" / "child_marker.py").write_text(
        "MIDDLEWARE = ['child-mw']\n",
        encoding="utf-8",
    )
    (package / "subagents" / "reviewer" / "__init__.py").write_text(
        "SUBAGENT = {'id': 'reviewer', 'name': 'reviewer', 'tools': '*', "
        "'middleware': '*', 'skills': '*', 'memory': '*'}\n",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(
        "AGENT = {'id': 'main', 'tools': '*', 'middleware': '*', 'skills': '*', "
        "'memory': '*', 'subagents': '*'}\n",
        encoding="utf-8",
    )

    resolved = resolve_agent(Settings(deepagents_main_agent=f"{package / '__init__.py'}:AGENT"))

    assert resolved["tools"][0]("ok") == "ok:ok"
    assert resolved["middleware"] == ["mw"]
    assert resolved["skills"] == [str(package / "skills" / "alpha")]
    assert resolved["memory"] == [str(package / "memory" / "project.md")]
    assert resolved["subagents"][0]["name"] == "reviewer"
    assert resolved["subagents"][0]["tools"][0]("child") == "child:CHILD"
    assert resolved["subagents"][0]["middleware"] == ["child-mw"]
    assert resolved["subagents"][0]["skills"] == [
        str(package / "subagents" / "reviewer" / "skills" / "beta")
    ]
    assert resolved["subagents"][0]["memory"] == [
        str(package / "subagents" / "reviewer" / "memory" / "review.md")
    ]


def test_env_example_stays_user_facing_and_small() -> None:
    env_example = (Path(__file__).resolve().parents[1] / ".env.example").read_text(
        encoding="utf-8"
    )
    assert "DEEPAGENTS_RUNTIME_DRIVER" not in env_example
    assert "DEEPAGENTS_RUNTIME_DATABASE_URL" not in env_example
    assert "DEEPAGENTS_RUNTIME_FACTORY" not in env_example
    assert "DEEPAGENTS_SANDBOX_KIND" not in env_example
    assert "DEEPAGENTS_BACKEND_SPEC" not in env_example
    assert "DEEPAGENTS_CHECKPOINT_BACKEND=sqlite" in env_example
    assert "BACKEND_LOG_LEVEL=info" in env_example
    assert "DEEPAGENTS_UPLOAD_ROOT_DIR=./data/uploads" in env_example
    assert "DEEPAGENTS_SANDBOX_ROOT_DIR=./data/sandbox" in env_example
    assert "DEEPAGENTS_SANDBOX_PROFILE=safe" in env_example
    active_settings = [
        line
        for line in env_example.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert len(active_settings) <= 13


def test_sandbox_profiles_permissions_and_custom_backend(tmp_path: Path) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    settings = Settings(deepagents_sandbox_profile="shell")
    assert settings.sandbox_settings() == {
        "kind": "local_shell",
        "root_dir": str((backend_root / "data" / "sandbox").resolve()),
        "skills_root_dir": str((backend_root / "agents" / "skills").resolve()),
        "uploads_root_dir": str((backend_root / "data" / "uploads").resolve()),
        "virtual_mode": True,
        "timeout": 120,
        "max_output_bytes": 100_000,
        "inherit_env": False,
        "env": {},
        "backend_spec": None,
    }
    rules = resolve_permissions(
        (
            {"operations": ["read"], "paths": ["/workspace"]},
            {"operations": ["write"], "paths": ["/workspace/out"]},
        )
    )
    assert rules[-1].mode == "deny"

    backend = resolve_backend(SandboxConfig.from_mapping(settings.sandbox_settings()))
    assert backend.__class__.__name__ == "CompositeBackend"
    assert [skill["name"] for skill in _list_skills(backend, "/skills")] == ["skill-creator"]
    assert backend.write("/skills/skill-creator/new.md", "nope").error == "read-only route"
    assert backend.write("/uploads/session/upload/file.txt", "nope").error == (
        "read-only route"
    )

    module = tmp_path / "backend_mod.py"
    module.write_text(
        "from deepagents.backends import StateBackend\n\n"
        "def build_backend():\n    return StateBackend()\n",
        encoding="utf-8",
    )
    backend = resolve_backend(
        SandboxConfig.from_mapping(
            {
                "kind": "custom",
                "backend_spec": f"{module}:build_backend",
                "skills_root_dir": str((backend_root / "agents" / "skills").resolve()),
                "uploads_root_dir": str((backend_root / "data" / "uploads").resolve()),
            }
        )
    )
    assert backend.__class__.__name__ == "CompositeBackend"
    assert backend.default.__class__.__name__ == "StateBackend"
    assert [skill["name"] for skill in _list_skills(backend, "/skills")] == ["skill-creator"]
    assert backend.write("/uploads/session/upload/file.txt", "nope").error == (
        "read-only route"
    )


def test_shell_sandbox_preserves_native_filesystem_permissions(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr("app.agent.build_model", lambda settings, model_id=None: object())
    monkeypatch.setattr(
        "app.agent.create_deep_agent",
        lambda **kwargs: captured.update(kwargs) or object(),
    )

    build_deep_agent(Settings(deepagents_sandbox_profile="shell"))

    assert captured["permissions"][0].operations == ["read"]
    assert captured["backend"].__class__.__name__ == "CompositeBackend"
    assert captured["backend"].default.__class__.__name__ == "LocalShellBackend"
    assert captured["skills"] == ["/skills/skill-creator"]
    assert captured["memory"] == ["/memory/project.md"]
    assert "/subagents/code-reviewer/skills/" in captured["backend"].routes
    assert "/subagents/code-reviewer/memory/" in captured["backend"].routes
    assert all(
        "permissions" in subagent and subagent["permissions"]
        for subagent in captured["subagents"]
        if isinstance(subagent, dict)
    )
    assert captured["subagents"][0]["skills"] == [
        "/subagents/code-reviewer/skills/review-checklist"
    ]
    assert "memory" not in captured["subagents"][0]
    subagent_memory = [
        middleware
        for middleware in captured["subagents"][0]["middleware"]
        if middleware.__class__.__name__ == "MemoryMiddleware"
    ]
    assert len(subagent_memory) == 1
    assert subagent_memory[0].sources == ["/subagents/code-reviewer/memory/review.md"]


def test_event_sequence_allocation_is_atomic_in_process(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'seq.db'}")
    database.initialize_schema()
    with database.session() as db:
        user = UserRecord(username="admin", password_hash="hash")
        db.add(user)
        db.flush()
        session = SessionRecord(owner_user_id=user.id, title="Seq", thread_id="thread-seq")
        db.add(session)
        db.flush()
        session_id = session.id

    def write(kind: str) -> None:
        with database.session() as db:
            append_event(db, session_id=session_id, kind=kind, type="step")

    threads = [threading.Thread(target=write, args=(kind,)) for kind in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    with database.session() as db:
        assert [row.seq for row in db.query(EventRecord).order_by(EventRecord.seq)] == [1, 2]


def test_event_append_integrity_error_preserves_outer_transaction(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'event-conflict.db'}")
    database.initialize_schema()

    with pytest.raises(IntegrityError):
        with database.session() as db:
            user = UserRecord(username="admin", password_hash="hash")
            db.add(user)
            db.flush()
            session = SessionRecord(owner_user_id=user.id, title="Seq", thread_id="thread-seq")
            db.add(session)
            db.flush()
            run = RunRecord(
                session_id=session.id,
                user_id=user.id,
                thread_id=session.thread_id,
                agent_id="agent",
                model_id="model",
                status="running",
                started_at=now(),
            )
            db.add(run)
            db.flush()
            with patch(
                "app.db._next_event_seq",
                side_effect=IntegrityError("synthetic", {}, Exception("conflict")),
            ):
                append_event(db, session_id=session.id, run_id=run.id, kind="conflict", type="step")

            raise AssertionError("append_event unexpectedly swallowed the conflict")

    with database.session() as db:
        assert db.scalar(select(RunRecord)) is None
        assert db.scalar(select(EventRecord)) is None


def test_run_lifecycle_persists_runs_events_and_native_context(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    graph = FakeGraph()
    monkeypatch.setattr(
        "app.routes.runtime_agent.build_deep_agent",
        lambda settings, model_id=None: graph,
    )
    session = client.post(
        "/api/sessions",
        headers=auth_headers,
        json={"metadata": {"topic": "tests"}},
    ).json()

    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/runs/stream",
        headers=auth_headers,
        json={"prompt": "hello"},
    ) as response:
        assert response.status_code == 200
        payloads = sse_events(response.read().decode())

    labels = [event["label"] for event in payloads]
    assert labels[:4] == [
        "run.started",
        "user.message",
        "middleware.applied",
        "prompt.compiled",
    ]
    assert labels[-1] == "run.completed"
    assert labels.count("run.completed") == 1
    assert "step.completed" in labels
    assert labels.count("assistant.message") == 1
    assert "sandbox.started" in labels
    assert "sandbox.completed" in labels
    assert [event["id"] for event in payloads] == [str(i) for i in range(1, len(payloads) + 1)]
    assert graph.configs[0]["configurable"]["thread_id"] == session["thread_id"]
    assert graph.contexts[0].session_id == session["id"]
    assert graph.contexts[0].session_metadata == {"topic": "tests"}

    history = client.get(f"/api/sessions/{session['id']}/events", headers=auth_headers).json()
    assert [event["kind"] for event in history] == labels
    prompt_audit = next(event for event in history if event["kind"] == "prompt.compiled")
    assert prompt_audit["redaction"] == "hash"
    assert "compiled_prompt_hash" in prompt_audit["payload"]
    assert "hello" not in json.dumps(prompt_audit)
    assert any(event["tool_name"] == "echo" for event in history)
    tool_event = next(
        event
        for event in history
        if event["kind"] == "tool.completed" and event["tool_name"] == "echo"
    )
    assert tool_event["payload"]["input"] == {"text": "hello"}
    assert tool_event["payload"]["output"] == {"text": "echo:hello"}
    sandbox_event = next(
        event
        for event in history
        if event["kind"] == "sandbox.completed" and event["tool_name"] == "execute"
    )
    assert sandbox_event["payload"]["input"] == {"command": "python -c 'print(1)'"}
    assert sandbox_event["payload"]["output"] == {"stdout": "1\n", "stderr": ""}

    with client.app.state.database.session() as db:
        run = db.scalar(select(RunRecord).where(RunRecord.session_id == session["id"]))
        assert run is not None
        assert run.status == "completed"
        assert run.thread_id == session["thread_id"]
        assert run.model_id == "test/fake"
        assert run.ended_at is not None


def test_backend_debug_logging_captures_concise_runtime_summaries(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    graph = FakeGraph()
    client.app.state.settings.backend_log_level = "debug"
    caplog.set_level(logging.DEBUG, logger="app.routes")
    monkeypatch.setattr(
        "app.routes.runtime_agent.build_deep_agent",
        lambda settings, model_id=None: graph,
    )
    session = client.post("/api/sessions", headers=auth_headers, json={}).json()

    response = client.post(
        f"/api/sessions/{session['id']}/runs",
        headers=auth_headers,
        json={"prompt": "hello"},
    )

    assert response.status_code == 201
    assert "runtime event" in caplog.text
    assert "on_tool_start" in caplog.text
    assert "raw_event" not in caplog.text
    assert "UPLOAD_E2E_MARKER" not in caplog.text


def test_first_prompt_updates_placeholder_session_title(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.routes.runtime_agent.build_deep_agent",
        lambda settings, model_id=None: ChainEndOnlyGraph(),
    )
    session = client.post("/api/sessions", headers=auth_headers, json={}).json()

    response = client.post(
        f"/api/sessions/{session['id']}/runs",
        headers=auth_headers,
        json={"prompt": "第一条消息会成为标题\n第二行不进入标题"},
    )

    assert response.status_code == 201
    updated = client.get(f"/api/sessions/{session['id']}", headers=auth_headers).json()
    assert updated["title"] == "第一条消息会成为标题"


def test_database_backed_session_accepts_second_turn_with_same_thread_context(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    class ThreadMemoryGraph:
        def __init__(self) -> None:
            self.prompts_by_thread: dict[str, list[str]] = {}

        async def astream_events(
            self,
            agent_input: Any,
            *,
            config: Any,
            context: Any,
        ) -> AsyncIterator[dict[str, Any]]:
            del context
            thread_id = str(config["configurable"]["thread_id"])
            prompt = str(agent_input["messages"][-1]["content"])
            prior_prompts = self.prompts_by_thread.setdefault(thread_id, [])
            text = f"prior:{prior_prompts[-1]}" if prior_prompts else "prior:none"
            yield {
                "event": "on_chat_model_end",
                "name": "model",
                "data": {"output": AIMessage(content=text)},
            }
            yield {
                "event": "on_chain_end",
                "name": "deepagents-web",
                "data": {"output": {"messages": [AIMessage(content=text)]}},
            }
            prior_prompts.append(prompt)

    graph = ThreadMemoryGraph()
    monkeypatch.setattr(
        "app.routes.runtime_agent.build_deep_agent",
        lambda settings, model_id=None: graph,
    )
    session = client.post("/api/sessions", headers=auth_headers, json={}).json()

    first = client.post(
        f"/api/sessions/{session['id']}/runs",
        headers=auth_headers,
        json={"prompt": "第一轮"},
    )
    second = client.post(
        f"/api/sessions/{session['id']}/runs",
        headers=auth_headers,
        json={"prompt": "第二轮"},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    history = client.get(f"/api/sessions/{session['id']}/events", headers=auth_headers).json()
    user_messages = [event for event in history if event["kind"] == "user.message"]
    assistant_messages = [event for event in history if event["kind"] == "assistant.message"]
    assert [event["content"] for event in user_messages] == ["第一轮", "第二轮"]
    assert assistant_messages[-1]["payload"]["message"]["content"] == "prior:第一轮"

    with client.app.state.database.session() as db:
        runs = db.scalars(
            select(RunRecord)
            .where(RunRecord.session_id == session["id"])
            .order_by(RunRecord.started_at)
        ).all()
        assert [run.status for run in runs] == ["completed", "completed"]
        assert {run.thread_id for run in runs} == {session["thread_id"]}


def test_session_context_survives_backend_restart_from_product_events(
    tmp_path: Path,
    monkeypatch,
) -> None:
    observed_inputs: list[list[dict[str, str]]] = []

    class TranscriptGraph:
        async def astream_events(
            self,
            agent_input: Any,
            *,
            config: Any,
            context: Any,
        ) -> AsyncIterator[dict[str, Any]]:
            del config, context
            messages = list(agent_input["messages"])
            observed_inputs.append(messages)
            user_contents = [
                str(message["content"])
                for message in messages
                if message.get("role") == "user"
            ]
            text = f"Transcript users: {' | '.join(user_contents)}"
            yield {
                "event": "on_chat_model_end",
                "name": "model",
                "data": {"output": AIMessage(content=text)},
            }
            yield {
                "event": "on_chain_end",
                "name": "deepagents-web",
                "data": {"output": {"messages": [AIMessage(content=text)]}},
            }

    models_path = tmp_path / "models.json"
    models_path.write_text(
        '{"model":"test/fake","provider":{"test":{"name":"Test","options":{},'
        '"models":{"fake":{"name":"Fake","model":"fake"}}}}}',
        encoding="utf-8",
    )
    database_url = f"sqlite+pysqlite:///{tmp_path / 'restart.db'}"

    def make_settings() -> Settings:
        return Settings(
            database_url=database_url,
            admin_email="admin@example.com",
            admin_username="admin",
            admin_password="secret",
            admin_token_secret="test-secret-key-with-32-bytes-minimum",
            deepagents_model_config_path=str(models_path),
        )

    monkeypatch.setattr(
        "app.routes.runtime_agent.build_deep_agent",
        lambda settings, model_id=None: TranscriptGraph(),
    )

    with TestClient(create_app(make_settings())) as first_client:
        first_headers = login_headers(first_client)
        session = first_client.post("/api/sessions", headers=first_headers, json={}).json()
        first = first_client.post(
            f"/api/sessions/{session['id']}/runs",
            headers=first_headers,
            json={"prompt": "第一轮"},
        )
        assert first.status_code == 201

    with TestClient(create_app(make_settings())) as second_client:
        second_headers = login_headers(second_client)
        second = second_client.post(
            f"/api/sessions/{session['id']}/runs",
            headers=second_headers,
            json={"prompt": "第二轮"},
        )
        assert second.status_code == 201

    assert observed_inputs[-1] == [
        {"role": "user", "content": "第一轮"},
        {"role": "assistant", "content": "Transcript users: 第一轮"},
        {"role": "user", "content": "第二轮"},
    ]


def test_uploads_are_stored_as_sandbox_readable_run_attachments(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    graph = FakeGraph()
    client.app.state.settings.audit_compiled_prompts = "full"
    monkeypatch.setattr(
        "app.routes.runtime_agent.build_deep_agent",
        lambda settings, model_id=None: graph,
    )
    session = client.post("/api/sessions", headers=auth_headers, json={}).json()
    assert re.match(r"^[a-f0-9]{12}$", session["id"])
    upload = client.post(
        f"/api/sessions/{session['id']}/uploads",
        headers=auth_headers,
        files={"file": ("notes.txt", b"UPLOAD_E2E_MARKER", "text/plain")},
    )
    assert upload.status_code == 201
    attachment = upload.json()
    assert re.match(r"^[a-f0-9]{12}$", attachment["id"])
    assert attachment["session_id"] == session["id"]
    assert attachment["path"] == f"/uploads/{session['id']}/notes.txt"
    assert attachment["content_type"] == "text/plain"

    with client.app.state.database.session() as db:
        upload_record = db.get(UploadRecord, attachment["id"])
        assert upload_record is not None
        assert upload_record.session_id == session["id"]
        assert upload_record.filename == "notes.txt"
        assert upload_record.storage_path == f"{session['id']}/notes.txt"
        assert len(
            db.scalars(
                select(UploadRecord).where(
                    UploadRecord.session_id == session["id"],
                    UploadRecord.user_id == upload_record.user_id,
                    UploadRecord.filename == "notes.txt",
                )
            ).all()
        ) == 1

    windows_named_upload = client.post(
        f"/api/sessions/{session['id']}/uploads",
        headers=auth_headers,
        files={"file": (r"C:\Users\me\notes.txt", b"UPLOAD_WINDOWS_MARKER", "text/plain")},
    )
    assert windows_named_upload.status_code == 201
    assert windows_named_upload.json()["id"] == attachment["id"]
    assert windows_named_upload.json()["name"] == "notes.txt"
    assert windows_named_upload.json()["path"] == f"/uploads/{session['id']}/notes.txt"
    with client.app.state.database.session() as db:
        upload_rows = db.scalars(
            select(UploadRecord).where(
                UploadRecord.session_id == session["id"],
                UploadRecord.filename == "notes.txt",
            )
        ).all()
        assert len(upload_rows) == 1

    backend = resolve_backend(
        SandboxConfig.from_mapping(client.app.state.settings.sandbox_settings())
    )
    assert "UPLOAD_WINDOWS_MARKER" in backend.read(attachment["path"]).file_data["content"]

    download = client.get(f"/api/uploads/{attachment['id']}/content", headers=auth_headers)
    assert download.status_code == 200
    assert download.content == b"UPLOAD_WINDOWS_MARKER"

    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/runs/stream",
        headers=auth_headers,
        json={"prompt": "read my upload", "attachments": [attachment, attachment]},
    ) as response:
        assert response.status_code == 200
        _ = sse_events(response.read().decode())

        assert len(graph.contexts[-1].current_attachments) == 1
        assert graph.contexts[-1].current_attachments[0]["path"] == attachment["path"]
        assert graph.contexts[-1].attachments[0]["path"] == attachment["path"]
        assert graph.inputs[-1]["messages"][-1]["content"] == "read my upload"

    delete = client.delete(f"/api/uploads/{attachment['id']}", headers=auth_headers)
    assert delete.status_code == 204
    missing = client.get(f"/api/uploads/{attachment['id']}/content", headers=auth_headers)
    assert missing.status_code == 404
    with client.app.state.database.session() as db:
        upload_record = db.get(UploadRecord, attachment["id"])
        assert upload_record is not None
        assert upload_record.status == "deleted"
        assert upload_record.deleted_at is not None

    history = client.get(f"/api/sessions/{session['id']}/events", headers=auth_headers).json()
    user_event = next(
        event
        for event in history
        if event["kind"] == "user.message" and event["content"] == "read my upload"
    )
    assert len(user_event["payload"]["attachments"]) == 1
    assert user_event["payload"]["attachments"][0]["path"] == attachment["path"]
    system_event = next(
        event
        for event in history
        if event["kind"] == "system.message" and attachment["path"] in str(event["content"])
    )
    assert system_event["role"] == "system"
    assert "UPLOAD" not in system_event["content"]
    assert attachment["path"] in system_event["content"]
    assert system_event["payload"]["source"] == "middleware.InjectAttachmentContextMessage"
    assert system_event["payload"]["attachment_count"] == 1
    assert system_event["payload"]["attachment_paths"] == [attachment["path"]]
    middleware_event = next(
        event
        for event in history
        if event["kind"] == "middleware.applied"
        and event["payload"]["attachment_paths"] == [attachment["path"]]
    )
    assert middleware_event["payload"]["attachment_count"] == 1
    audit_event = next(
        event
        for event in history
        if event["kind"] == "prompt.compiled"
        and event["payload"]["compiled_prompt"] == "read my upload"
    )
    assert audit_event["payload"]["compiled_prompt"] == "read my upload"
    assert audit_event["payload"]["context"]["attachment_count"] == 1
    assert audit_event["payload"]["context"]["attachments"][0]["path"] == attachment["path"]


def test_upload_context_message_replays_from_database_when_checkpoint_missing(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    graph = FakeGraph()
    monkeypatch.setattr(
        "app.routes.runtime_agent.build_deep_agent",
        lambda settings, model_id=None: graph,
    )
    session = client.post("/api/sessions", headers=auth_headers, json={}).json()
    upload = client.post(
        f"/api/sessions/{session['id']}/uploads",
        headers=auth_headers,
        files={"file": ("notes.txt", b"UPLOAD_E2E_MARKER", "text/plain")},
    ).json()

    first = client.post(
        f"/api/sessions/{session['id']}/runs",
        headers=auth_headers,
        json={"prompt": "read my upload", "attachments": [upload]},
    )
    second = client.post(
        f"/api/sessions/{session['id']}/runs",
        headers=auth_headers,
        json={"prompt": "continue without checkpoint"},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    replayed = graph.inputs[-1]["messages"]
    assert [message["role"] for message in replayed] == [
        "user",
        "system",
        "assistant",
        "user",
    ]
    assert replayed[1]["content"].count(upload["path"]) == 1
    assert replayed[-1]["content"] == "continue without checkpoint"


def test_run_attachment_resolution_rejects_upload_ids_from_other_sessions(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.routes.runtime_agent.build_deep_agent",
        lambda settings, model_id=None: FakeGraph(),
    )
    first = client.post("/api/sessions", headers=auth_headers, json={}).json()
    second = client.post("/api/sessions", headers=auth_headers, json={}).json()
    upload = client.post(
        f"/api/sessions/{first['id']}/uploads",
        headers=auth_headers,
        files={"file": ("notes.txt", b"SESSION_ONE", "text/plain")},
    ).json()

    response = client.post(
        f"/api/sessions/{second['id']}/runs",
        headers=auth_headers,
        json={"prompt": "read other session", "attachments": [upload]},
    )

    assert response.status_code == 404
    assert "Upload not found" in response.text

    mismatched_session = client.post(
        f"/api/sessions/{first['id']}/runs",
        headers=auth_headers,
        json={
            "prompt": "read mismatched metadata",
            "attachments": [{**upload, "session_id": second["id"]}],
        },
    )

    assert mismatched_session.status_code == 404
    assert "Upload not found" in mismatched_session.text


def test_chain_end_only_runs_still_emit_one_final_assistant_message(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.routes.runtime_agent.build_deep_agent",
        lambda settings, model_id=None: ChainEndOnlyGraph(),
    )
    session = client.post("/api/sessions", headers=auth_headers, json={}).json()
    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/runs/stream",
        headers=auth_headers,
        json={"prompt": "hello"},
    ) as response:
        payloads = sse_events(response.read().decode())
    finals = [event for event in payloads if event["label"] == "assistant.message"]
    assert [event["data"]["message"]["content"] for event in finals] == ["Simple final"]


def test_nested_completion_events_do_not_overwrite_final_assistant_message(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.routes.runtime_agent.build_deep_agent",
        lambda settings, model_id=None: NestedCompletionGraph(),
    )
    session = client.post("/api/sessions", headers=auth_headers, json={}).json()
    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/runs/stream",
        headers=auth_headers,
        json={"prompt": "hello"},
    ) as response:
        payloads = sse_events(response.read().decode())
    finals = [event for event in payloads if event["label"] == "assistant.message"]
    assert [event["data"]["message"]["content"] for event in finals] == ["Real final"]
    assert [event["label"] for event in payloads].count("run.completed") == 1
    skills = [event for event in payloads if event["type"] == "skill"]
    assert [event["label"] for event in skills] == ["skill.completed"]
    assert skills[0]["data"]["skills"] == []


def test_tool_interleaved_model_turns_keep_each_assistant_message(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.routes.runtime_agent.build_deep_agent",
        lambda settings, model_id=None: ToolInterleavedModelGraph(),
    )
    session = client.post("/api/sessions", headers=auth_headers, json={}).json()
    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/runs/stream",
        headers=auth_headers,
        json={"prompt": "hello"},
    ) as response:
        payloads = sse_events(response.read().decode())

    finals = [event for event in payloads if event["label"] == "assistant.message"]
    assert [event["data"]["message"]["content"] for event in finals] == [
        "准备调用工具",
        "工具完成",
    ]
    assert [event["label"] for event in payloads].count("run.completed") == 1
    assert any(event["label"] == "sandbox.completed" for event in payloads)


def test_runtime_failure_updates_run_without_completion(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    class FailingGraph:
        async def astream_events(
            self,
            agent_input: Any,
            *,
            config: Any,
            context: Any,
        ) -> AsyncIterator[dict[str, Any]]:
            del agent_input, config, context
            yield {
                "event": "on_chain_error",
                "name": "agent",
                "data": {"error": ValueError("boom")},
            }

    monkeypatch.setattr(
        "app.routes.runtime_agent.build_deep_agent",
        lambda settings, model_id=None: FailingGraph(),
    )
    session = client.post("/api/sessions", headers=auth_headers, json={}).json()
    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/runs/stream",
        headers=auth_headers,
        json={"prompt": "hello"},
    ) as response:
        payloads = sse_events(response.read().decode())

    labels = [event["label"] for event in payloads]
    assert "run.failed" in labels
    assert "run.completed" not in labels
    with client.app.state.database.session() as db:
        run = db.scalar(select(RunRecord).where(RunRecord.session_id == session["id"]))
        assert run is not None
        assert run.status == "failed"


def test_runtime_stops_persisting_events_after_run_is_cancelled(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    class SelfCancellingGraph:
        async def astream_events(
            self,
            agent_input: Any,
            *,
            config: Any,
            context: Any,
        ) -> AsyncIterator[dict[str, Any]]:
            del agent_input, config
            yield {
                "event": "on_chat_model_stream",
                "name": "model",
                "data": {"chunk": {"content": "before-cancel"}},
            }
            with client.app.state.database.session() as db:
                run = db.get(RunRecord, context.run_id)
                assert run is not None
                run.cancel_requested_at = now()
                run.status = "cancelled"
                run.ended_at = now()
                run.session.status = "idle"
                append_event(
                    db,
                    session_id=run.session_id,
                    run_id=run.id,
                    kind="run.cancelled",
                    type="status",
                    payload={"status": "cancelled", "terminal": True},
                )
            yield {
                "event": "on_chat_model_stream",
                "name": "model",
                "data": {"chunk": {"content": "after-cancel"}},
            }
            yield {
                "event": "on_chain_end",
                "name": "deepagents-web",
                "data": {"output": {"messages": [AIMessage(content="after-cancel")]}},
            }

    monkeypatch.setattr(
        "app.routes.runtime_agent.build_deep_agent",
        lambda settings, model_id=None: SelfCancellingGraph(),
    )
    session = client.post("/api/sessions", headers=auth_headers, json={}).json()

    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/runs/stream",
        headers=auth_headers,
        json={"prompt": "stop me"},
    ) as response:
        assert response.status_code == 200
        _ = sse_events(response.read().decode())

    history = client.get(f"/api/sessions/{session['id']}/events", headers=auth_headers).json()
    encoded_history = json.dumps(history)
    assert "before-cancel" in encoded_history
    assert "after-cancel" not in encoded_history
    assert [event["kind"] for event in history].count("run.cancelled") == 1
    assert "run.completed" not in [event["kind"] for event in history]


def test_delete_session_cancels_active_run_and_stops_runtime_persistence(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    class DeleteSessionGraph:
        async def astream_events(
            self,
            agent_input: Any,
            *,
            config: Any,
            context: Any,
        ) -> AsyncIterator[dict[str, Any]]:
            del agent_input, config
            yield {
                "event": "on_chat_model_stream",
                "name": "model",
                "data": {"chunk": {"content": "before-delete"}},
            }
            with client.app.state.database.session() as db:
                session = db.get(SessionRecord, context.session_id)
                assert session is not None
                _archive_session(db, client.app.state.settings, session.owner, session)
            yield {
                "event": "on_chat_model_stream",
                "name": "model",
                "data": {"chunk": {"content": "after-delete"}},
            }
            yield {
                "event": "on_chain_end",
                "name": "deepagents-web",
                "data": {"output": {"messages": [AIMessage(content="after-delete")]}},
            }

    monkeypatch.setattr(
        "app.routes.runtime_agent.build_deep_agent",
        lambda settings, model_id=None: DeleteSessionGraph(),
    )
    session = client.post("/api/sessions", headers=auth_headers, json={}).json()

    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/runs/stream",
        headers=auth_headers,
        json={"prompt": "delete me while running"},
    ) as response:
        assert response.status_code == 200
        _ = sse_events(response.read().decode())

    assert client.get(f"/api/sessions/{session['id']}", headers=auth_headers).status_code == 404
    with client.app.state.database.session() as db:
        run = db.scalar(select(RunRecord).where(RunRecord.session_id == session["id"]))
        assert run is not None
        assert run.status == "cancelled"
        assert run.cancel_requested_at is not None
        events = db.scalars(
            select(EventRecord)
            .where(EventRecord.session_id == session["id"])
            .order_by(EventRecord.seq)
        ).all()
    encoded_events = json.dumps([event.payload | {"kind": event.kind} for event in events])
    assert "before-delete" in encoded_events
    assert "after-delete" not in encoded_events
    assert [event.kind for event in events].count("run.cancelled") == 1
    cancelled = next(event for event in events if event.kind == "run.cancelled")
    assert cancelled.payload["reason"] == "session_archived"
    assert "run.completed" not in [event.kind for event in events]


def test_checkpoint_probe_failure_is_not_treated_as_missing_checkpoint() -> None:
    settings = Settings(deepagents_checkpoint_backend="memory")
    settings.__dict__["runtime_components"] = {
        "checkpointer": FailingCheckpoint(),
        "store": object(),
        "cache": object(),
    }

    with pytest.raises(RuntimeError, match="Runtime checkpoint probe failed"):
        asyncio.run(
            _runtime_input_messages(
                settings,
                {"configurable": {"thread_id": "thread-1"}},
                {
                    "prompt": "current",
                    "messages": [
                        {"role": "user", "content": "previous"},
                        {"role": "user", "content": "current"},
                    ],
                },
            )
        )


def test_ready_endpoint_reports_safe_checks(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["schema"] == "ok"
    assert body["checks"]["runtime_persistence"] == "ok"
    assert "secret" not in json.dumps(body).lower()


def test_ready_endpoint_reports_runtime_probe_failures(client: TestClient) -> None:
    client.app.state.settings.__dict__["runtime_components"] = {
        "checkpointer": FailingCheckpoint(),
        "store": object(),
        "cache": object(),
    }

    response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["checks"]["runtime_persistence"] == "error"


def test_static_backend_contracts() -> None:
    app_root = Path(__file__).resolve().parents[1] / "app"
    haystack = "\n".join(path.read_text(encoding="utf-8") for path in app_root.rglob("*.py"))
    assert "runtime_blobs" not in haystack
    assert "app." + "services" not in haystack
    assert "app." + "core" not in haystack
    assert "replay" not in haystack.lower()
    assert "create_deep_agent" in (app_root / "agent.py").read_text(encoding="utf-8")


def test_default_agent_selection_uses_local_skill_and_memory_registries() -> None:
    resolved = resolve_agent(Settings())
    assert resolved["skills"] == [
        str(
            (
                Path(__file__).resolve().parents[1]
                / "agents"
                / "skills"
                / "skill-creator"
            ).resolve()
        )
    ]
    assert resolved["memory"] == [
        str((Path(__file__).resolve().parents[1] / "agents" / "memory" / "project.md").resolve())
    ]
    assert resolved["permissions"][0].paths[:6] == [
        "/workspace/main",
        "/workspace/main/**",
        "/skills",
        "/skills/**",
        "/uploads",
        "/uploads/**",
    ]


def test_routes_do_not_import_package_middleware_for_initial_events() -> None:
    routes_source = (Path(__file__).resolve().parents[1] / "app" / "routes.py").read_text(
        encoding="utf-8"
    )

    assert "agents.middleware" not in routes_source
    assert "attachment_context_content" not in routes_source
    assert "ATTACHMENT_CONTEXT_SOURCE" not in routes_source
    assert "app.runtime.attachment_context" not in routes_source


def test_run_context_supports_mapping_style_access_for_middleware() -> None:
    context = DeepAgentsRunContext(
        session_id="session-1",
        run_id="run-1",
        username="admin",
        thread_id="thread-1",
        session_metadata={"topic": "chat"},
    )
    assert context.get("session_id") == "session-1"
    assert context.get("current_attachments") == ()
    assert ("thread_id", "thread-1") in context.items()


def test_attachment_context_middleware_lists_only_current_uploads() -> None:
    class RuntimeStub:
        context = {
            "current_attachments": (
                {"name": "AGENTS.md", "path": "/uploads/59824c2c/AGENTS.md"},
            )
        }

    result = asyncio.run(inject_attachment_context_message.abefore_model({}, RuntimeStub()))

    assert result is not None
    content = result["messages"][0].content
    assert "exactly 1 file(s)" in content
    assert content.count("- AGENTS.md:") == 1
    assert "untrusted data" in content
    assert "do not infer sibling files" in content
    events = inject_attachment_context_message.deepagents_initial_events(RuntimeStub.context)
    assert events[0]["kind"] == "system.message"
    assert events[0]["content"] == content
    assert events[0]["payload"]["source"] == "middleware.InjectAttachmentContextMessage"


def test_mysql_schema_initialization_creates_database_if_needed(monkeypatch) -> None:
    statements: list[str] = []
    created_urls: list[str] = []
    disposed: list[str] = []

    class FakeConnection:
        def __enter__(self) -> FakeConnection:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
            return False

        def exec_driver_sql(self, statement: str) -> None:
            statements.append(statement)

    class FakeEngine:
        def begin(self) -> FakeConnection:
            return FakeConnection()

        def dispose(self) -> None:
            disposed.append("disposed")

    def fake_create_engine(url: Any, *args: Any, **kwargs: Any) -> FakeEngine:
        del args, kwargs
        created_urls.append(str(url))
        if "deepagents_platform" in str(url) and not statements:
            raise AssertionError("target MySQL database engine was created before bootstrap")
        return FakeEngine()

    monkeypatch.setattr("app.db.create_engine", fake_create_engine)
    database = Database("mysql+pymysql://root:secret@127.0.0.1:3306/deepagents_platform")
    monkeypatch.setattr(
        "app.db.Base.metadata.create_all",
        lambda engine: statements.append("create_all"),
    )
    database.initialize_schema()
    assert created_urls[0].startswith("mysql+pymysql://root:***@127.0.0.1:3306/mysql")
    assert "deepagents_platform" in created_urls[1]
    assert statements[0] == "CREATE DATABASE IF NOT EXISTS `deepagents_platform`"
    assert statements[-1] == "create_all"
    assert disposed == ["disposed"]
