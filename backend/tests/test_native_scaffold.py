from __future__ import annotations

import json
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from deepagents.middleware.skills import _list_skills
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import inspect, select

from app.agent import DeepAgentsRunContext, build_deep_agent
from app.auth import hash_password
from app.catalog import build_model, model_options, resolve_agent, resolve_permissions
from app.db import Database, EventRecord, RunRecord, SessionRecord, UserRecord, append_event
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

    async def astream_events(
        self,
        agent_input: Any,
        *,
        version: str,
        config: Any,
        context: Any,
    ) -> AsyncIterator[dict[str, Any]]:
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
        version: str,
        config: Any,
        context: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        del agent_input, version, config, context
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
        version: str,
        config: Any,
        context: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        del agent_input, version, config, context
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
        version: str,
        config: Any,
        context: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        del agent_input, version, config, context
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


def test_runtime_resolver_memory_factory_and_official_sqlite_guidance(tmp_path: Path) -> None:
    memory = Settings(deepagents_runtime_driver="memory")
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

    sqlite = Settings(deepagents_runtime_driver="sqlite")
    try:
        _ = sqlite.runtime_components
    except RuntimeError as exc:
        assert "DEEPAGENTS_RUNTIME_DATABASE_URL" in str(exc)
    else:
        raise AssertionError("official sqlite runtime accepted missing runtime database URL")

    shared = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'product.db'}",
        deepagents_runtime_driver="sqlite",
        deepagents_runtime_database_url=f"sqlite+pysqlite:///{tmp_path / 'product.db'}",
    )
    try:
        _ = shared.runtime_components
    except RuntimeError as exc:
        assert "must be different from DATABASE_URL" in str(exc)
    else:
        raise AssertionError("official sqlite runtime accepted shared product database URL")

    equivalent_shared = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'shared.db'}",
        deepagents_runtime_driver="sqlite",
        deepagents_runtime_database_url=f"sqlite:///{tmp_path / 'shared.db'}",
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
        deepagents_runtime_driver="postgres",
        deepagents_runtime_database_url=(
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
        deepagents_runtime_driver="postgres",
        deepagents_runtime_database_url=(
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
        deepagents_runtime_driver="postgres",
        deepagents_runtime_database_url=(
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
        deepagents_runtime_driver="postgres",
        deepagents_runtime_database_url=(
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
        deepagents_runtime_driver="postgres",
        deepagents_runtime_database_url=f"sqlite+pysqlite:///{tmp_path / 'runtime.db'}",
    )
    try:
        _ = wrong_family.runtime_components
    except RuntimeError as exc:
        assert "requires a postgresql" in str(exc)
    else:
        raise AssertionError("postgres runtime accepted non-postgres runtime DB URL")

    sqlite = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'product.db'}",
        deepagents_runtime_driver="sqlite",
        deepagents_runtime_database_url=f"sqlite+pysqlite:///{tmp_path / 'runtime.db'}",
    )
    try:
        _ = sqlite.runtime_components
    except RuntimeError as exc:
        assert "langgraph-checkpoint-sqlite" in str(exc)
    else:
        raise AssertionError("sqlite runtime unexpectedly materialized without official package")


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
                " 'builtin_tools': ['read_file'], 'disabled_builtin_tools': ['execute'],",
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
    assert resolved["builtin_tool_allowlist"] == ("read_file",)
    assert resolved["builtin_tool_blocklist"] == ("execute",)
    assert resolved["subagents"][0]["name"] == "reviewer"


def test_sandbox_profiles_permissions_and_custom_backend(tmp_path: Path) -> None:
    settings = Settings(deepagents_sandbox_profile="shell")
    assert settings.sandbox_settings() == {
        "kind": "local_shell",
        "root_dir": str((Path(__file__).resolve().parents[1] / "agents").resolve()),
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
    assert [skill["name"] for skill in _list_skills(backend, "/skills")] == ["skill-creator"]

    module = tmp_path / "backend_mod.py"
    module.write_text(
        "from deepagents.backends import StateBackend\n\n"
        "def build_backend():\n    return StateBackend()\n",
        encoding="utf-8",
    )
    backend = resolve_backend(
        SandboxConfig.from_mapping({"kind": "custom", "backend_spec": f"{module}:build_backend"})
    )
    assert backend.__class__.__name__ == "StateBackend"


def test_shell_sandbox_omits_deepagents_permissions(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr("app.agent.build_model", lambda settings, model_id=None: object())
    monkeypatch.setattr(
        "app.agent.create_deep_agent",
        lambda **kwargs: captured.update(kwargs) or object(),
    )

    build_deep_agent(Settings(deepagents_sandbox_profile="shell"))

    assert captured["permissions"] is None
    assert captured["backend"].__class__.__name__ == "LocalShellBackend"
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
            version: str,
            config: Any,
            context: Any,
        ) -> AsyncIterator[dict[str, Any]]:
            del agent_input, version, config, context
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
    assert resolved["skills"] == ["/skills"]
    assert resolved["memory"] == [
        str((Path(__file__).resolve().parents[1] / "agents" / "memory" / "project.md").resolve())
    ]
    assert resolved["permissions"][0].paths[:2] == ["/workspace/main", "/workspace/main/**"]


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


def test_mysql_schema_initialization_creates_database_if_needed(monkeypatch) -> None:
    statements: list[str] = []
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

    monkeypatch.setattr("app.db.create_engine", lambda *args, **kwargs: FakeEngine())
    database = Database("mysql+pymysql://root:secret@127.0.0.1:3306/deepagents_platform")
    monkeypatch.setattr(
        "app.db.Base.metadata.create_all",
        lambda engine: statements.append("create_all"),
    )
    database.initialize_schema()
    assert statements[0] == "CREATE DATABASE IF NOT EXISTS `deepagents_platform`"
    assert statements[-1] == "create_all"
    assert disposed == ["disposed"]
