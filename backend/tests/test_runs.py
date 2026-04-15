from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi.testclient import TestClient
from langgraph.errors import GraphRecursionError

from app.core.config import Settings
from app.db.models import (
    AgentRunRecord,
    MessageRecord,
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


class CancellableRuntime:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.cancelled = threading.Event()

    async def astream_events(
        self,
        agent_input: Any,
        *,
        version: str = "v2",
        config: Any = None,
    ) -> AsyncIterator[dict[str, Any]]:
        self.started.set()
        yield {
            "event": "on_chain_start",
            "name": "deep-agent",
            "run_id": "runtime-cancellable",
            "metadata": {"langgraph_node": "agent"},
            "data": {"input": agent_input},
        }
        try:
            while True:
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


def build_failing_runtime(_config: Any) -> Any:
    raise RuntimeError("builder exploded")


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


class MultiMessageRuntime:
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
            "run_id": "runtime-multi",
            "metadata": {"langgraph_node": "agent"},
            "data": {"input": agent_input},
        }
        yield {
            "event": "on_chat_model_end",
            "name": "model",
            "run_id": "runtime-multi",
            "metadata": {"langgraph_node": "model"},
            "data": {"output": {"messages": [{"content": "让我检查一下目录的内容"}]}},
        }
        yield {
            "event": "on_tool_start",
            "name": "search_files",
            "run_id": "runtime-multi",
            "data": {"input": {"path": "extensions/skills"}},
        }
        yield {
            "event": "on_tool_end",
            "name": "search_files",
            "run_id": "runtime-multi",
            "data": {"output": {"text": "empty"}},
        }
        yield {
            "event": "on_chat_model_end",
            "name": "model",
            "run_id": "runtime-multi",
            "metadata": {"langgraph_node": "model"},
            "data": {"output": {"messages": [{"content": "目录是空的"}]}},
        }
        yield {
            "event": "on_chain_end",
            "name": "deep-agent",
            "run_id": "runtime-multi",
            "metadata": {"langgraph_node": "agent"},
            "data": {"output": {"messages": [{"content": "目录是空的"}]}},
        }


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


def test_cancel_run_marks_run_cancelled_and_terminates_stream(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'cancel.db'}",
        admin_email="admin@example.com",
        admin_username="admin",
        admin_password="secret",
        admin_token_secret="test-secret",
        upload_storage_dir=tmp_path / "uploads",
        deepagents_model="openai:gpt-5.4",
    )
    app = create_app(settings)
    runtime = CancellableRuntime()
    app.state.run_service.builder = lambda _config: runtime

    with TestClient(app) as client:
        login = client.post("/api/admin/login", json={"username": "admin", "password": "secret"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        session = client.post("/api/sessions", headers=headers, json={"title": "Cancelable run"})
        session_id = session.json()["id"]

        run = client.post(
            "/api/runs",
            headers=headers,
            json={"session_id": session_id, "prompt": "wait here", "attachments": []},
        )
        assert run.status_code == 201
        run_id = run.json()["run_id"]
        assert runtime.started.wait(timeout=1)

        cancel = client.post(f"/api/runs/{run_id}/cancel", headers=headers)
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancelled"

        run_detail = client.get(f"/api/runs/{run_id}", headers=headers)
        assert run_detail.status_code == 200
        assert run_detail.json()["status"] == "cancelled"

        with client.stream("GET", f"/api/runs/{run_id}/stream?access_token={token}") as response:
            payloads = []
            for line in response.iter_lines():
                if isinstance(line, bytes):
                    line = line.decode("utf-8")
                if not line or not line.startswith("data: "):
                    continue
                payloads.append(json.loads(line[6:]))

    assert runtime.cancelled.wait(timeout=1)
    assert any(
        payload["type"] == "status" and payload["status"] == "cancelled" for payload in payloads
    )
    assert not any(
        payload["type"] == "status" and payload["status"] == "completed" for payload in payloads
    )
    assert not any(payload["type"] == "message.final" for payload in payloads)

    with app.state.database.session_factory() as db:
        run_record = db.query(AgentRunRecord).filter(AgentRunRecord.id == run_id).first()
        assert run_record is not None
        assert run_record.status == "cancelled"
        assert run_record.completed_at is not None
        assert run_record.error_text == "DeepAgents run was stopped by the user."
        assert (
            db.query(RunEventViewRecord)
            .filter(
                RunEventViewRecord.run_id == run_id,
                RunEventViewRecord.status == "cancelled",
            )
            .count()
            == 1
        )


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


def test_run_start_persists_distilled_session_title_without_overwriting_it(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'titles.db'}",
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
        session = client.post("/api/sessions", headers=headers, json={})
        session_id = session.json()["id"]

        for prompt in ("第一条消息应该成为会话标题", "第二条消息不能覆盖标题"):
            run = client.post(
                "/api/runs",
                headers=headers,
                json={"session_id": session_id, "prompt": prompt, "attachments": []},
            )
            run_id = run.json()["run_id"]
            with client.stream(
                "GET",
                f"/api/runs/{run_id}/stream?access_token={token}",
            ) as response:
                for line in response.iter_lines():
                    if isinstance(line, bytes):
                        line = line.decode("utf-8")
                    if line and '"status": "completed"' in line:
                        break

    with app.state.database.session_factory() as db:
        record = db.query(SessionRecord).filter(SessionRecord.id == session_id).first()
        assert record is not None
        assert record.title == "第一条消息应该成为会话标题"


def test_intermediate_assistant_messages_do_not_complete_the_run_or_disappear(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'multi-message.db'}",
        admin_email="admin@example.com",
        admin_username="admin",
        admin_password="secret",
        admin_token_secret="test-secret",
        upload_storage_dir=tmp_path / "uploads",
        deepagents_model="openai:gpt-5.4",
    )
    app = create_app(settings)
    app.state.run_service.builder = lambda _config: MultiMessageRuntime()

    with TestClient(app) as client:
        login = client.post("/api/admin/login", json={"username": "admin", "password": "secret"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        session = client.post("/api/sessions", headers=headers, json={"title": "Multi message"})
        session_id = session.json()["id"]

        run = client.post(
            "/api/runs",
            headers=headers,
            json={
                "session_id": session_id,
                "prompt": "看一下 extensions/skills",
                "attachments": [],
            },
        )
        run_id = run.json()["run_id"]

        payloads = []
        with client.stream("GET", f"/api/runs/{run_id}/stream?access_token={token}") as response:
            for line in response.iter_lines():
                if isinstance(line, bytes):
                    line = line.decode("utf-8")
                if not line or not line.startswith("data: "):
                    continue
                payload = json.loads(line[6:])
                payloads.append(payload)
                if payload["type"] == "status" and payload["status"] == "completed":
                    break

        interim_index = next(
            index
            for index, payload in enumerate(payloads)
            if payload["type"] == "message.final"
            and payload["message"]["content"] == "让我检查一下目录的内容"
        )
        tool_index = next(
            index for index, payload in enumerate(payloads) if payload["type"] == "tool"
        )
        completion_index = next(
            index
            for index, payload in enumerate(payloads)
            if payload["type"] == "status" and payload["status"] == "completed"
        )

        assert payloads[interim_index]["status"] == "running"
        assert interim_index < tool_index < completion_index
        assert any(
            payload["type"] == "message.final"
            and payload["message"]["content"] == "目录是空的"
            for payload in payloads
        )

        transcript = client.get(f"/api/sessions/{session_id}/messages", headers=headers)
        contents = [item["content"] for item in transcript.json()]
        assert contents == ["看一下 extensions/skills", "让我检查一下目录的内容", "目录是空的"]

        with app.state.database.session_factory() as db:
            assistant_rows = (
                db.query(MessageRecord)
                .filter(MessageRecord.session_id == session_id, MessageRecord.role == "assistant")
                .order_by(MessageRecord.created_at.asc(), MessageRecord.id.asc())
                .all()
            )
            assert [
                (row.content, row.is_final)
                for row in assistant_rows
            ] == [
                ("让我检查一下目录的内容", False),
                ("目录是空的", True),
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
            and "执行过程没有在预期步数内收敛" in payload["message"]["content"]
            for payload in payloads
        )

        transcript = client.get(f"/api/sessions/{session_id}/messages", headers=headers)
        contents = [item["content"] for item in transcript.json()]
        assert any("执行过程没有在预期步数内收敛" in content for content in contents)


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

    assert any("run started with 1 attachment" in message for message in messages)
    assert any(
        "resolved runtime config using" in message
        or "resolved custom model config using" in message
        for message in messages
    )
    assert any("DeepAgents agent is ready" in message for message in messages)
    assert any(
        "stream started with 1 input message and 1 attachment" in message
        for message in messages
    )
    assert any(
        "stream finished with" in message and "runtime events" in message for message in messages
    )
    assert any(
        "run completed with" in message and "runtime events" in message for message in messages
    )
    assert any('"event": "run.created"' in message for message in messages)
    assert any('"phase": "streaming"' in message for message in messages)
    assert all("super secret prompt" not in message for message in messages)
    assert all("top-secret attachment" not in message for message in messages)
    assert any('"runtime_run_id": "runtime-1"' in message for message in messages)


def test_run_failure_logging_identifies_the_failing_phase(tmp_path, caplog) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'logging-failure.db'}",
        admin_email="admin@example.com",
        admin_username="admin",
        admin_password="secret",
        admin_token_secret="test-secret",
        upload_storage_dir=tmp_path / "uploads",
        deepagents_model="openai:gpt-5.4",
    )
    app = create_app(settings)
    app.state.run_service.builder = build_failing_runtime

    caplog.set_level(logging.INFO, logger="app.services.runs")

    with TestClient(app) as client:
        login = client.post("/api/admin/login", json={"username": "admin", "password": "secret"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        session = client.post("/api/sessions", headers=headers, json={"title": "Logging failure"})
        session_id = session.json()["id"]

        run = client.post(
            "/api/runs",
            headers=headers,
            json={"session_id": session_id, "prompt": "hello", "attachments": []},
        )
        run_id = run.json()["run_id"]

        with client.stream("GET", f"/api/runs/{run_id}/stream?access_token={token}") as response:
            for line in response.iter_lines():
                if isinstance(line, bytes):
                    line = line.decode("utf-8")
                if line and '"status": "failed"' in line:
                    break

    messages = [record.getMessage() for record in caplog.records]

    assert any("run failed while building agent" in message for message in messages)
    assert any('"phase": "building agent"' in message for message in messages)
    assert any(
        '"next_step": "inspect the DeepAgents builder and runtime dependencies"' in message
        for message in messages
    )


def test_run_builds_attachment_context_with_storage_key_and_upload_path(tmp_path) -> None:
    runtime = CapturingConversationRuntime()
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'attachments.db'}",
        admin_email="admin@example.com",
        admin_username="admin",
        admin_password="secret",
        admin_token_secret="test-secret",
        upload_storage_dir=tmp_path / "uploads",
        deepagents_model="openai:gpt-5.4",
    )
    app = create_app(settings)
    app.state.run_service.builder = lambda _config: runtime

    with TestClient(app) as client:
        login = client.post("/api/admin/login", json={"username": "admin", "password": "secret"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        session = client.post("/api/sessions", headers=headers, json={"title": "Uploads demo"})
        session_id = session.json()["id"]

        upload = client.post(
            f"/api/sessions/{session_id}/uploads",
            headers=headers,
            files={"file": ("notes.txt", b"hello upload", "text/plain")},
        )
        upload_payload = upload.json()

        run = client.post(
            "/api/runs",
            headers=headers,
            json={
                "session_id": session_id,
                "prompt": "请读取刚上传的文件",
                "attachments": [
                    {
                        "id": upload_payload["id"],
                        "name": upload_payload["filename"],
                        "status": "uploaded",
                    }
                ],
            },
        )
        run_id = run.json()["run_id"]

        with client.stream("GET", f"/api/runs/{run_id}/stream?access_token={token}") as response:
            for line in response.iter_lines():
                if isinstance(line, bytes):
                    line = line.decode("utf-8")
                if line and '"status": "completed"' in line:
                    break

    assert runtime.inputs
    content = runtime.inputs[0]["messages"][0]["content"]
    expected_upload_path = (settings.upload_storage_dir / upload_payload["storage_key"]).resolve()

    assert "Attached files:" in content
    assert "notes.txt" in content
    assert upload_payload["storage_key"] in content
    assert str(expected_upload_path) in content
    assert "Prefer sandbox_path for filesystem tools when it is provided" in content


def test_run_builds_attachment_context_with_sandbox_path_for_virtual_filesystem(tmp_path) -> None:
    runtime = CapturingConversationRuntime()
    sandbox_root = tmp_path / "sandbox-root"
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'attachments-virtual.db'}",
        admin_email="admin@example.com",
        admin_username="admin",
        admin_password="secret",
        admin_token_secret="test-secret",
        upload_storage_dir=sandbox_root / "uploads",
        deepagents_model="openai:gpt-5.4",
        deepagents_sandbox_kind="filesystem",
        deepagents_sandbox_root_dir=str(sandbox_root),
        deepagents_sandbox_virtual_mode=True,
    )
    app = create_app(settings)
    app.state.run_service.builder = lambda _config: runtime

    with TestClient(app) as client:
        login = client.post("/api/admin/login", json={"username": "admin", "password": "secret"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        session = client.post("/api/sessions", headers=headers, json={"title": "Uploads demo"})
        session_id = session.json()["id"]

        upload = client.post(
            f"/api/sessions/{session_id}/uploads",
            headers=headers,
            files={"file": ("notes.txt", b"hello upload", "text/plain")},
        )
        upload_payload = upload.json()

        run = client.post(
            "/api/runs",
            headers=headers,
            json={
                "session_id": session_id,
                "prompt": "请读取刚上传的文件",
                "attachments": [
                    {
                        "id": upload_payload["id"],
                        "name": upload_payload["filename"],
                        "status": "uploaded",
                    }
                ],
            },
        )
        run_id = run.json()["run_id"]

        with client.stream("GET", f"/api/runs/{run_id}/stream?access_token={token}") as response:
            for line in response.iter_lines():
                if isinstance(line, bytes):
                    line = line.decode("utf-8")
                if line and '"status": "completed"' in line:
                    break

    assert runtime.inputs
    content = runtime.inputs[0]["messages"][0]["content"]
    expected_upload_path = (settings.upload_storage_dir / upload_payload["storage_key"]).resolve()
    expected_sandbox_path = f"/uploads/{upload_payload['storage_key']}"

    assert f"sandbox_path={expected_sandbox_path}" in content
    assert f"upload_path={expected_upload_path}" in content
    assert "Prefer sandbox_path for filesystem tools when it is provided" in content


def test_single_uploaded_file_tasks_use_generic_attachment_context_without_keyword_hint(
    tmp_path,
) -> None:
    runtime = CapturingConversationRuntime()
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'single-file.db'}",
        admin_email="admin@example.com",
        admin_username="admin",
        admin_password="secret",
        admin_token_secret="test-secret",
        upload_storage_dir=tmp_path / "uploads",
        deepagents_model="openai:gpt-5.4",
    )
    app = create_app(settings)
    app.state.run_service.builder = lambda _config: runtime

    with TestClient(app) as client:
        login = client.post("/api/admin/login", json={"username": "admin", "password": "secret"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        session = client.post("/api/sessions", headers=headers, json={"title": "Single file"})
        session_id = session.json()["id"]

        upload = client.post(
            f"/api/sessions/{session_id}/uploads",
            headers=headers,
            files={"file": ("notes.txt", b"hello upload", "text/plain")},
        )
        upload_payload = upload.json()

        run = client.post(
            "/api/runs",
            headers=headers,
            json={
                "session_id": session_id,
                "prompt": "What is in this file?",
                "attachments": [
                    {
                        "id": upload_payload["id"],
                        "name": upload_payload["filename"],
                        "status": "uploaded",
                    }
                ],
            },
        )
        run_id = run.json()["run_id"]

        with client.stream("GET", f"/api/runs/{run_id}/stream?access_token={token}") as response:
            for line in response.iter_lines():
                if isinstance(line, bytes):
                    line = line.decode("utf-8")
                if line and '"status": "completed"' in line:
                    break

    assert runtime.inputs
    content = runtime.inputs[0]["messages"][0]["content"]

    assert "Attached files:" in content
    assert "Single-file execution hint:" not in content
    assert "directly as the first tool action" not in content


def test_run_input_hook_can_customize_attachment_prompt_injection(tmp_path) -> None:
    runtime = CapturingConversationRuntime()
    hook_module = tmp_path / "run_hooks.py"
    hook_module.write_text(
        "\n".join(
            [
                "def inject(context):",
                "    names = ','.join(item['name'] for item in context.attachments)",
                "    return {'content': context.content + '\\n\\nCUSTOM_ATTACHMENTS=' + names}",
            ]
        ),
        encoding="utf-8",
    )
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'custom-hook.db'}",
        admin_email="admin@example.com",
        admin_username="admin",
        admin_password="secret",
        admin_token_secret="test-secret",
        upload_storage_dir=tmp_path / "uploads",
        deepagents_model="openai:gpt-5.4",
        deepagents_run_input_hook_specs=f"{hook_module}:inject",
    )
    app = create_app(settings)
    app.state.run_service.builder = lambda _config: runtime

    with TestClient(app) as client:
        login = client.post("/api/admin/login", json={"username": "admin", "password": "secret"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        session = client.post("/api/sessions", headers=headers, json={"title": "Custom hook"})
        session_id = session.json()["id"]

        upload = client.post(
            f"/api/sessions/{session_id}/uploads",
            headers=headers,
            files={"file": ("notes.txt", b"hello upload", "text/plain")},
        )
        upload_payload = upload.json()

        run = client.post(
            "/api/runs",
            headers=headers,
            json={
                "session_id": session_id,
                "prompt": "Use the upload",
                "attachments": [
                    {
                        "id": upload_payload["id"],
                        "name": upload_payload["filename"],
                        "status": "uploaded",
                    }
                ],
            },
        )
        run_id = run.json()["run_id"]

        with client.stream("GET", f"/api/runs/{run_id}/stream?access_token={token}") as response:
            for line in response.iter_lines():
                if isinstance(line, bytes):
                    line = line.decode("utf-8")
                if line and '"status": "completed"' in line:
                    break

    assert runtime.inputs
    content = runtime.inputs[0]["messages"][0]["content"]
    assert "CUSTOM_ATTACHMENTS=notes.txt" in content
    assert "Attached files:" not in content
