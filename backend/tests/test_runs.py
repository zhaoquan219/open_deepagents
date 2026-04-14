from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi.testclient import TestClient
from langgraph.errors import GraphRecursionError

from app.core.config import Settings
from app.db.models import (
    AgentRunRecord,
    RunEventViewRecord,
    SessionRecord,
    SessionRuntimeLinkRecord,
)
from app.main import create_app


class FakeRuntime:
    async def astream_events(
        self,
        agent_input: Any,
        *,
        version: str = "v2",
        config: Any = None,
    ) -> AsyncIterator[dict[str, Any]]:
        yield {
            "event": "on_chain_start",
            "name": "deep-agent",
            "run_id": "runtime-1",
            "metadata": {"langgraph_node": "agent"},
            "data": {"input": agent_input},
        }
        yield {
            "event": "on_chat_model_stream",
            "name": "model",
            "run_id": "runtime-1",
            "metadata": {"langgraph_node": "model"},
            "data": {"chunk": {"content": [{"text": "Hello "}, {"text": "world"}]}},
        }
        yield {
            "event": "on_chat_model_end",
            "name": "model",
            "run_id": "runtime-1",
            "metadata": {"langgraph_node": "model"},
            "data": {"output": {"messages": [{"content": "Hello world"}]}},
        }
        yield {
            "event": "on_chain_end",
            "name": "deep-agent",
            "run_id": "runtime-1",
            "metadata": {"langgraph_node": "agent"},
            "data": {"output": {"messages": [{"content": "Hello world"}]}},
        }


def build_fake_runtime(_config: Any) -> FakeRuntime:
    return FakeRuntime()


class BlockingToolRuntime:
    async def astream_events(
        self,
        agent_input: Any,
        *,
        version: str = "v2",
        config: Any = None,
    ) -> AsyncIterator[dict[str, Any]]:
        yield {
            "event": "on_chain_start",
            "name": "deep-agent",
            "run_id": "runtime-blocking",
            "metadata": {"langgraph_node": "agent"},
            "data": {"input": agent_input},
        }
        yield {
            "event": "on_tool_start",
            "name": "echo_tool",
            "run_id": "runtime-blocking",
            "data": {"input": {"text": "ping"}},
        }
        time.sleep(0.05)
        yield {
            "event": "on_tool_end",
            "name": "echo_tool",
            "run_id": "runtime-blocking",
            "data": {"output": {"text": "echo:ping"}},
        }
        yield {
            "event": "on_chat_model_end",
            "name": "model",
            "run_id": "runtime-blocking",
            "metadata": {"langgraph_node": "model"},
            "data": {"output": {"messages": [{"content": "done"}]}},
        }
        yield {
            "event": "on_chain_end",
            "name": "deep-agent",
            "run_id": "runtime-blocking",
            "metadata": {"langgraph_node": "agent"},
            "data": {"output": {"messages": [{"content": "done"}]}},
        }


def build_blocking_tool_runtime(_config: Any) -> BlockingToolRuntime:
    return BlockingToolRuntime()


class CapturingConversationRuntime:
    def __init__(self) -> None:
        self.inputs: list[Any] = []

    async def astream_events(
        self,
        agent_input: Any,
        *,
        version: str = "v2",
        config: Any = None,
    ) -> AsyncIterator[dict[str, Any]]:
        self.inputs.append(agent_input)
        reply = f"seen:{len(agent_input.get('messages', []))}"
        yield {
            "event": "on_chain_start",
            "name": "deep-agent",
            "run_id": f"runtime-capture-{len(self.inputs)}",
            "metadata": {"langgraph_node": "agent"},
            "data": {"input": agent_input},
        }
        yield {
            "event": "on_chat_model_end",
            "name": "model",
            "run_id": f"runtime-capture-{len(self.inputs)}",
            "metadata": {"langgraph_node": "model"},
            "data": {"output": {"messages": [{"content": reply}]}},
        }
        yield {
            "event": "on_chain_end",
            "name": "deep-agent",
            "run_id": f"runtime-capture-{len(self.inputs)}",
            "metadata": {"langgraph_node": "agent"},
            "data": {"output": {"messages": [{"content": reply}]}},
        }


class RecursingRuntime:
    async def astream_events(
        self,
        agent_input: Any,
        *,
        version: str = "v2",
        config: Any = None,
    ) -> AsyncIterator[dict[str, Any]]:
        yield {
            "event": "on_chain_start",
            "name": "deep-agent",
            "run_id": "runtime-recursing",
            "metadata": {"langgraph_node": "agent"},
            "data": {"input": agent_input},
        }
        raise GraphRecursionError("Recursion limit of 25 reached without hitting a stop condition.")


def test_run_lifecycle_and_stream(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'runs.db'}",
        admin_email="admin@example.com",
        admin_username="admin",
        admin_password="secret",
        admin_token_secret="test-secret",
        upload_storage_dir=tmp_path / "uploads",
        deepagents_model="openai:gpt-5.4",
    )
    app = create_app(settings)
    app.state.run_service.builder = build_fake_runtime

    with TestClient(app) as client:
        login = client.post("/api/admin/login", json={"username": "admin", "password": "secret"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        session = client.post("/api/sessions", headers=headers, json={"title": "Run demo"})
        session_id = session.json()["id"]

        run = client.post(
            "/api/runs",
            headers=headers,
            json={"session_id": session_id, "prompt": "Say hello", "attachments": []},
        )
        assert run.status_code == 201
        run_id = run.json()["run_id"]

        with client.stream("GET", f"/api/runs/{run_id}/stream?access_token={token}") as response:
            payloads = []
            for line in response.iter_lines():
                if isinstance(line, bytes):
                    line = line.decode("utf-8")
                if not line or not line.startswith("data: "):
                    continue
                payloads.append(line[6:])
                if '"status": "completed"' in line:
                    break

        assert any('"type": "status"' in line for line in payloads)
        assert any('"type": "message.delta"' in line for line in payloads)
        assert any('"type": "message.final"' in line for line in payloads)

        transcript = client.get(f"/api/sessions/{session_id}/messages", headers=headers)
        assert transcript.status_code == 200
        contents = [item["content"] for item in transcript.json()]
        assert "Say hello" in contents
        assert "Hello world" in contents

        with app.state.database.session_factory() as db:
            run_record = db.query(AgentRunRecord).filter(AgentRunRecord.id == run_id).first()
            assert run_record is not None
            assert run_record.status == "completed"
            assert run_record.final_output_text == "Hello world"
            assert run_record.runtime_run_id == "runtime-1"

            event_views = (
                db.query(RunEventViewRecord)
                .filter(RunEventViewRecord.run_id == run_id)
                .order_by(RunEventViewRecord.sequence.asc())
                .all()
            )
            assert len(event_views) >= 4
            assert any(event.event_type == "message.final" for event in event_views)

            runtime_link = (
                db.query(SessionRuntimeLinkRecord)
                .filter(SessionRuntimeLinkRecord.session_id == session_id)
                .first()
            )
            assert runtime_link is not None
            assert runtime_link.runtime_run_id == "runtime-1"


def test_stream_emits_keepalive_while_tool_execution_blocks(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'keepalive.db'}",
        admin_email="admin@example.com",
        admin_username="admin",
        admin_password="secret",
        admin_token_secret="test-secret",
        upload_storage_dir=tmp_path / "uploads",
        deepagents_model="openai:gpt-5.4",
    )
    app = create_app(settings)
    app.state.database.create_all()
    app.state.run_service.builder = build_blocking_tool_runtime
    app.state.run_manager.keepalive_interval = 0.01

    with app.state.database.session_factory() as db:
        session = SessionRecord(title="Blocking tool")
        db.add(session)
        db.commit()
        session_id = session.id

    async def collect_chunks() -> list[str]:
        run = app.state.run_service.start_run(
            settings=settings,
            session_id=session_id,
            prompt="use the tool",
            attachments=[],
        )
        stream = app.state.run_manager.stream(run.run_id)
        chunks: list[str] = []
        try:
            async for chunk in stream:
                chunks.append(chunk)
                if chunk == ": keep-alive\n\n":
                    break
        finally:
            await stream.aclose()

        return chunks

    try:
        chunks = asyncio.run(collect_chunks())
    finally:
        app.state.database.dispose()

    assert any('"type": "tool"' in chunk for chunk in chunks)
    assert ": keep-alive\n\n" in chunks


def test_stream_route_resumes_from_last_event_id_header(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'resume.db'}",
        admin_email="admin@example.com",
        admin_username="admin",
        admin_password="secret",
        admin_token_secret="test-secret",
        upload_storage_dir=tmp_path / "uploads",
        deepagents_model="openai:gpt-5.4",
    )
    app = create_app(settings)
    run_state = app.state.run_manager.create("session-1")
    run_state.publish(
        {
            "event_id": f"{run_state.run_id}:000001",
            "type": "status",
            "status": "running",
            "label": "Run started",
            "detail": "started",
        }
    )
    run_state.publish(
        {
            "event_id": f"{run_state.run_id}:000002",
            "type": "tool",
            "status": "running",
            "label": "tool.started",
            "detail": "search",
        }
    )
    run_state.finish("completed")

    with TestClient(app) as client:
        login = client.post("/api/admin/login", json={"username": "admin", "password": "secret"})
        token = login.json()["access_token"]

        with client.stream(
            "GET",
            f"/api/runs/{run_state.run_id}/stream?access_token={token}",
            headers={"Last-Event-ID": f"{run_state.run_id}:000001"},
        ) as response:
            payloads = []
            for line in response.iter_lines():
                if isinstance(line, bytes):
                    line = line.decode("utf-8")
                if not line or not line.startswith("data: "):
                    continue
                payloads.append(line[6:])

        assert [json.loads(payload) for payload in payloads] == [
            {
                "event_id": f"{run_state.run_id}:000002",
                "type": "tool",
                "status": "running",
                "label": "tool.started",
                "detail": "search",
            }
        ]


def test_second_run_receives_prior_session_messages(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'memory.db'}",
        admin_email="admin@example.com",
        admin_username="admin",
        admin_password="secret",
        admin_token_secret="test-secret",
        upload_storage_dir=tmp_path / "uploads",
        deepagents_model="openai:gpt-5.4",
    )
    app = create_app(settings)
    runtime = CapturingConversationRuntime()
    app.state.run_service.builder = lambda _config: runtime

    with TestClient(app) as client:
        login = client.post("/api/admin/login", json={"username": "admin", "password": "secret"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        session = client.post("/api/sessions", headers=headers, json={"title": "Memory demo"})
        session_id = session.json()["id"]

        for prompt in ("第一问", "第二问"):
            run = client.post(
                "/api/runs",
                headers=headers,
                json={"session_id": session_id, "prompt": prompt, "attachments": []},
            )
            run_id = run.json()["run_id"]
            stream_url = f"/api/runs/{run_id}/stream?access_token={token}"
            with client.stream("GET", stream_url) as response:
                for line in response.iter_lines():
                    if isinstance(line, bytes):
                        line = line.decode("utf-8")
                    if line and '"status": "completed"' in line:
                        break

    assert len(runtime.inputs) == 2
    assert runtime.inputs[0] == {"messages": [{"role": "user", "content": "第一问"}]}
    assert runtime.inputs[1]["messages"] == [
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "seen:1"},
        {"role": "user", "content": "第二问"},
    ]


def test_graph_recursion_error_returns_fallback_assistant_message(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'recursion.db'}",
        admin_email="admin@example.com",
        admin_username="admin",
        admin_password="secret",
        admin_token_secret="test-secret",
        upload_storage_dir=tmp_path / "uploads",
        deepagents_model="openai:gpt-5.4",
    )
    app = create_app(settings)
    app.state.run_service.builder = lambda _config: RecursingRuntime()

    with TestClient(app) as client:
        login = client.post("/api/admin/login", json={"username": "admin", "password": "secret"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        session = client.post("/api/sessions", headers=headers, json={"title": "Recursion demo"})
        session_id = session.json()["id"]
        client.post(
            f"/api/sessions/{session_id}/messages",
            headers=headers,
            json={"role": "assistant", "content": "当前目录为空，没有文件。"},
        )

        run = client.post(
            "/api/runs",
            headers=headers,
            json={"session_id": session_id, "prompt": "你当前是在什么目录？", "attachments": []},
        )
        run_id = run.json()["run_id"]

        payloads = []
        with client.stream("GET", f"/api/runs/{run_id}/stream?access_token={token}") as response:
            for line in response.iter_lines():
                if isinstance(line, bytes):
                    line = line.decode("utf-8")
                if line and line.startswith("data: "):
                    payloads.append(json.loads(line[6:]))
                if line and '"status": "completed"' in line:
                    break

        assert any(
            payload["type"] == "message.final"
            and "暂时无法给出更具体的目录路径" in payload["message"]["content"]
            for payload in payloads
        )

        transcript = client.get(f"/api/sessions/{session_id}/messages", headers=headers)
        contents = [item["content"] for item in transcript.json()]
        assert any("暂时无法给出更具体的目录路径" in content for content in contents)


def test_run_logging_captures_lifecycle_without_prompt_or_attachment_content(
    tmp_path,
    caplog,
) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'logging.db'}",
        admin_email="admin@example.com",
        admin_username="admin",
        admin_password="secret",
        admin_token_secret="test-secret",
        upload_storage_dir=tmp_path / "uploads",
        deepagents_model="openai:gpt-5.4",
    )
    app = create_app(settings)
    app.state.run_service.builder = build_fake_runtime

    caplog.set_level(logging.INFO, logger="app.services.runs")

    with TestClient(app) as client:
        login = client.post("/api/admin/login", json={"username": "admin", "password": "secret"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        session = client.post("/api/sessions", headers=headers, json={"title": "Logging demo"})
        session_id = session.json()["id"]

        run = client.post(
            "/api/runs",
            headers=headers,
            json={
                "session_id": session_id,
                "prompt": "super secret prompt",
                "attachments": [{"name": "secret.txt", "content": "top-secret attachment"}],
            },
        )
        run_id = run.json()["run_id"]

        with client.stream("GET", f"/api/runs/{run_id}/stream?access_token={token}") as response:
            for line in response.iter_lines():
                if isinstance(line, bytes):
                    line = line.decode("utf-8")
                if line and '"status": "completed"' in line:
                    break

    messages = [record.getMessage() for record in caplog.records]

    assert any("run.created" in message for message in messages)
    assert any("run.runtime_config" in message for message in messages)
    assert any("run.agent_build_completed" in message for message in messages)
    assert any("run.stream_started" in message for message in messages)
    assert any("run.completed" in message for message in messages)
    assert all("super secret prompt" not in message for message in messages)
    assert all("top-secret attachment" not in message for message in messages)
    assert any('"runtime_run_id": "runtime-1"' in message for message in messages)
