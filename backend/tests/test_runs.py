from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.models import AgentRunRecord, RunEventViewRecord, SessionRuntimeLinkRecord
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


def test_run_lifecycle_and_stream(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'runs.db'}",
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

        with client.app.state.database.session_factory() as db:
            run_record = db.query(AgentRunRecord).filter(AgentRunRecord.id == run_id).first()
            assert run_record is not None
            assert run_record.status == "completed"
            assert run_record.final_output_text == "Hello world"

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
