from __future__ import annotations

import asyncio
import json
import logging
import threading
import warnings
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from deepagents.middleware.skills import _list_skills
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import inspect, select

from agents.middleware.audit_middleware import inject_attachment_context_message
from app.agent import DeepAgentsRunContext, build_deep_agent
from app.auth import hash_password
from app.catalog import (
    build_model,
    load_model_catalog,
    model_options,
    resolve_agent,
    resolve_permissions,
    validate_model_catalog,
)
from app.db import (
    PRODUCT_TABLES,
    Database,
    EventRecord,
    RunRecord,
    SessionRecord,
    UserRecord,
    append_event,
)
from app.main import create_app
from app.runtime.extensions import SandboxConfig, resolve_backend
from app.settings import Settings


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


def test_schema_is_exact_four_table_product_projection(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    inspector = inspect(client.app.state.database.engine)
    assert set(inspector.get_table_names()) == {"events", "runs", "sessions", "users"}
    indexes = {
        table: {index["name"] for index in inspector.get_indexes(table)}
        for table in ("users", "sessions", "runs", "events")
    }
    assert "ix_users_email" in indexes["users"]
    assert "ix_sessions_owner_updated" in indexes["sessions"]
    assert "ix_runs_session_id" in indexes["runs"]
    assert "ix_runs_status" in indexes["runs"]
    assert "ix_events_run_id" in indexes["events"]
    assert "ix_events_kind" in indexes["events"]
    assert "ix_events_created_at" in indexes["events"]

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


def test_readiness_repairs_missing_product_tables(client: TestClient) -> None:
    with client.app.state.database.engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE events")

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    inspector = inspect(client.app.state.database.engine)
    assert PRODUCT_TABLES.issubset(set(inspector.get_table_names()))


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
                " 'permissions': [{'builtin_tools':['read_file'], 'paths':['/tmp']}],",
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
    assert resolved["builtin_tool_allowlist"] == ("read_file",)
    assert resolved["builtin_tool_blocklist"] is None
    assert resolved["subagents"][0]["name"] == "reviewer"


def test_agent_resolution_rejects_legacy_builtin_tool_schema(tmp_path: Path) -> None:
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
            {"builtin_tools": ["read_file"], "paths": ["/workspace"]},
            {"builtin_tools": ["write_file"], "paths": ["/workspace/out"]},
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


def test_shell_sandbox_omits_deepagents_permissions(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr("app.agent.build_model", lambda settings, model_id=None: object())
    monkeypatch.setattr(
        "app.agent.create_deep_agent",
        lambda **kwargs: captured.update(kwargs) or object(),
    )

    build_deep_agent(Settings(deepagents_sandbox_profile="shell"))

    assert captured["permissions"] is None
    assert captured["backend"].__class__.__name__ == "CompositeBackend"
    assert captured["backend"].default.__class__.__name__ == "LocalShellBackend"
    assert all(
        "permissions" not in subagent or subagent["permissions"] is None
        for subagent in captured["subagents"]
        if isinstance(subagent, dict)
    )


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

    with client.app.state.database.session() as db:
        run = db.scalar(select(RunRecord).where(RunRecord.session_id == session["id"]))
        assert run is not None
        assert run.status == "completed"
        assert run.thread_id == session["thread_id"]
        assert run.model_id == "test/fake"
        assert run.ended_at is not None


def test_backend_debug_logging_captures_raw_agent_updates(
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
    assert "agent update" in caplog.text
    assert "on_tool_start" in caplog.text
    assert "agent event" in caplog.text


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
    upload = client.post(
        f"/api/sessions/{session['id']}/uploads",
        headers=auth_headers,
        files={"file": ("notes.txt", b"UPLOAD_E2E_MARKER", "text/plain")},
    )
    assert upload.status_code == 201
    attachment = upload.json()
    short_session = session["id"].replace("-", "")[:8]
    assert attachment["id"] == f"{short_session}:notes.txt"
    assert attachment["path"] == f"/uploads/{short_session}/notes.txt"

    overwrite = client.post(
        f"/api/sessions/{session['id']}/uploads",
        headers=auth_headers,
        files={"file": ("notes.txt", b"UPLOAD_OVERWRITE_MARKER", "text/plain")},
    )
    assert overwrite.status_code == 201
    assert overwrite.json()["id"] == attachment["id"]
    assert overwrite.json()["path"] == attachment["path"]

    backend = resolve_backend(
        SandboxConfig.from_mapping(client.app.state.settings.sandbox_settings())
    )
    assert "UPLOAD_OVERWRITE_MARKER" in backend.read(attachment["path"]).file_data["content"]

    download = client.get(f"/api/uploads/{attachment['id']}/content", headers=auth_headers)
    assert download.status_code == 200
    assert download.content == b"UPLOAD_OVERWRITE_MARKER"

    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/runs/stream",
        headers=auth_headers,
        json={"prompt": "read my upload", "attachments": [attachment, attachment]},
    ) as response:
        assert response.status_code == 200
        _ = sse_events(response.read().decode())

        assert len(graph.contexts[0].current_attachments) == 1
        assert graph.contexts[0].current_attachments[0]["path"] == attachment["path"]
        assert graph.contexts[0].attachments[0]["path"] == attachment["path"]
        assert graph.inputs[0]["messages"][0]["content"] == "read my upload"

    delete = client.delete(f"/api/uploads/{attachment['id']}", headers=auth_headers)
    assert delete.status_code == 204
    missing = client.get(f"/api/uploads/{attachment['id']}/content", headers=auth_headers)
    assert missing.status_code == 404

    history = client.get(f"/api/sessions/{session['id']}/events", headers=auth_headers).json()
    user_event = next(event for event in history if event["kind"] == "user.message")
    assert len(user_event["payload"]["attachments"]) == 1
    assert user_event["payload"]["attachments"][0]["path"] == attachment["path"]
    system_event = next(event for event in history if event["kind"] == "system.message")
    assert system_event["role"] == "system"
    assert "UPLOAD" not in system_event["content"]
    assert attachment["path"] in system_event["content"]
    assert system_event["payload"]["source"] == "middleware.InjectAttachmentContextMessage"
    middleware_event = next(event for event in history if event["kind"] == "middleware.applied")
    assert middleware_event["payload"]["attachment_count"] == 1
    audit_event = next(event for event in history if event["kind"] == "prompt.compiled")
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


def test_ready_endpoint_reports_safe_checks(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["schema"] == "ok"
    assert "secret" not in json.dumps(body).lower()


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
    assert "Do not infer sibling files" in content


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
