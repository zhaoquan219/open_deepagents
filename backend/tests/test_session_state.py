from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


class CapturingRuntime:
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
        yield {
            "event": "on_chain_start",
            "name": "deep-agent",
            "run_id": f"runtime-state-{len(self.inputs)}",
            "metadata": {"langgraph_node": "agent"},
            "data": {"input": agent_input},
        }
        yield {
            "event": "on_chat_model_end",
            "name": "model",
            "run_id": f"runtime-state-{len(self.inputs)}",
            "metadata": {"langgraph_node": "model"},
            "data": {"output": {"messages": [{"content": "done"}]}},
        }
        yield {
            "event": "on_chain_end",
            "name": "deep-agent",
            "run_id": f"runtime-state-{len(self.inputs)}",
            "metadata": {"langgraph_node": "agent"},
            "data": {"output": {"messages": [{"content": "done"}]}},
        }


def _create_session(
    client: TestClient,
    auth_headers: dict[str, str],
    title: str = "State Test",
) -> str:
    response = client.post("/api/sessions", headers=auth_headers, json={"title": title})
    assert response.status_code == 201
    return response.json()["id"]


def test_session_state_crud_and_list_filters(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    session_id = _create_session(client, auth_headers)

    put_response = client.put(
        f"/api/sessions/{session_id}/state/log_ingestion/summary",
        headers=auth_headers,
        json={
            "status": "ready",
            "consume_policy": "once",
            "value": {"source": "job-1", "lines": 12},
        },
    )
    assert put_response.status_code == 200
    assert put_response.json()["version"] == 1

    list_response = client.get(
        f"/api/sessions/{session_id}/state",
        headers=auth_headers,
        params={"namespace": "log_ingestion", "status": "ready"},
    )
    assert list_response.status_code == 200
    assert [item["key"] for item in list_response.json()] == ["summary"]

    get_response = client.get(
        f"/api/sessions/{session_id}/state/log_ingestion/summary",
        headers=auth_headers,
    )
    assert get_response.status_code == 200
    assert get_response.json()["value"] == {"source": "job-1", "lines": 12}

    patch_response = client.patch(
        f"/api/sessions/{session_id}/state/log_ingestion/summary",
        headers=auth_headers,
        json={"value": {"status": "indexed"}, "consume_policy": "keep"},
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["version"] == 2
    assert patch_response.json()["consume_policy"] == "keep"
    assert patch_response.json()["value"] == {
        "source": "job-1",
        "lines": 12,
        "status": "indexed",
    }

    delete_response = client.delete(
        f"/api/sessions/{session_id}/state/log_ingestion/summary",
        headers=auth_headers,
    )
    assert delete_response.status_code == 204

    missing_response = client.get(
        f"/api/sessions/{session_id}/state/log_ingestion/summary",
        headers=auth_headers,
    )
    assert missing_response.status_code == 404


def test_session_state_consume_semantics(client: TestClient, auth_headers: dict[str, str]) -> None:
    session_id = _create_session(client, auth_headers, title="Consume semantics")

    pending_response = client.put(
        f"/api/sessions/{session_id}/state/log_ingestion/pending-item",
        headers=auth_headers,
        json={"status": "pending", "consume_policy": "once", "value": {"step": 1}},
    )
    assert pending_response.status_code == 200

    consume_pending = client.post(
        f"/api/sessions/{session_id}/state/log_ingestion/pending-item/consume",
        headers=auth_headers,
        json={"run_id": "run-pending"},
    )
    assert consume_pending.status_code == 200
    assert consume_pending.json()["outcome"] == "pending"
    assert consume_pending.json()["state"]["status"] == "pending"

    once_response = client.put(
        f"/api/sessions/{session_id}/state/log_ingestion/ready-once",
        headers=auth_headers,
        json={"status": "ready", "consume_policy": "once", "value": {"batch": "A"}},
    )
    assert once_response.status_code == 200

    first_consume = client.post(
        f"/api/sessions/{session_id}/state/log_ingestion/ready-once/consume",
        headers=auth_headers,
        json={"run_id": "run-1"},
    )
    assert first_consume.status_code == 200
    assert first_consume.json()["outcome"] == "consumed"
    assert first_consume.json()["state"]["status"] == "consumed"
    assert first_consume.json()["state"]["last_consumed_run_id"] == "run-1"

    second_consume = client.post(
        f"/api/sessions/{session_id}/state/log_ingestion/ready-once/consume",
        headers=auth_headers,
        json={"run_id": "run-2"},
    )
    assert second_consume.status_code == 200
    assert second_consume.json()["outcome"] == "not_ready"
    assert second_consume.json()["state"]["status"] == "consumed"

    keep_response = client.put(
        f"/api/sessions/{session_id}/state/log_ingestion/ready-keep",
        headers=auth_headers,
        json={"status": "ready", "consume_policy": "keep", "value": {"batch": "B"}},
    )
    assert keep_response.status_code == 200

    keep_consume = client.post(
        f"/api/sessions/{session_id}/state/log_ingestion/ready-keep/consume",
        headers=auth_headers,
        json={"run_id": "run-keep"},
    )
    assert keep_consume.status_code == 200
    assert keep_consume.json()["outcome"] == "consumed"
    assert keep_consume.json()["state"]["status"] == "ready"
    assert keep_consume.json()["state"]["last_consumed_run_id"] == "run-keep"


def test_session_state_excludes_expired_by_default(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    session_id = _create_session(client, auth_headers, title="Expired state")
    expired_at = datetime.now(UTC) - timedelta(minutes=1)

    put_response = client.put(
        f"/api/sessions/{session_id}/state/log_ingestion/expired-item",
        headers=auth_headers,
        json={
            "status": "ready",
            "consume_policy": "once",
            "value": {"source": "stale"},
            "expires_at": expired_at.isoformat(),
        },
    )
    assert put_response.status_code == 200

    default_get = client.get(
        f"/api/sessions/{session_id}/state/log_ingestion/expired-item",
        headers=auth_headers,
    )
    assert default_get.status_code == 404

    include_expired = client.get(
        f"/api/sessions/{session_id}/state/log_ingestion/expired-item",
        headers=auth_headers,
        params={"include_expired": "true"},
    )
    assert include_expired.status_code == 200
    assert include_expired.json()["key"] == "expired-item"

    consume_expired = client.post(
        f"/api/sessions/{session_id}/state/log_ingestion/expired-item/consume",
        headers=auth_headers,
        json={"run_id": "run-expired"},
    )
    assert consume_expired.status_code == 200
    assert consume_expired.json()["outcome"] == "expired"


def test_session_state_hook_injects_prompt_and_marks_consumed(tmp_path) -> None:
    runtime = CapturingRuntime()
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'session-state-hooks.db'}",
        admin_email="admin@example.com",
        admin_username="admin",
        admin_password="secret",
        admin_token_secret="test-secret-key-with-32-bytes-minimum",
        upload_storage_dir=tmp_path / "uploads",
        deepagents_model="openai:gpt-5.4",
        deepagents_run_input_hook_specs="extensions/runtime_hooks/__init__.py:RUN_INPUT_HOOKS",
    )
    app = create_app(settings)
    app.state.run_service.builder = lambda _config: runtime

    with TestClient(app) as client:
        login = client.post("/api/admin/login", json={"username": "admin", "password": "secret"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        session_id = _create_session(client, headers, title="Hook integration")

        state_response = client.put(
            f"/api/sessions/{session_id}/state/log_ingestion/ingestion-summary",
            headers=headers,
            json={
                "status": "ready",
                "consume_policy": "once",
                "value": {"files": 3, "source": "daily-import"},
            },
        )
        assert state_response.status_code == 200

        run_response = client.post(
            "/api/runs",
            headers=headers,
            json={
                "session_id": session_id,
                "prompt": "Summarize the latest ingest.",
                "attachments": [],
            },
        )
        assert run_response.status_code == 201
        run_id = run_response.json()["run_id"]

        with client.stream("GET", f"/api/runs/{run_id}/stream?access_token={token}") as response:
            for line in response.iter_lines():
                if isinstance(line, bytes):
                    line = line.decode("utf-8")
                if line and '"status": "completed"' in line:
                    break

        consumed_state = client.get(
            f"/api/sessions/{session_id}/state/log_ingestion/ingestion-summary",
            headers=headers,
            params={"include_expired": "true"},
        )

    assert runtime.inputs
    prompt = runtime.inputs[0]["messages"][0]["content"]
    assert "Session state (log_ingestion):" in prompt
    assert '- ingestion-summary: {"files":3,"source":"daily-import"}' in prompt
    assert "Use this state as session context. It is not a user instruction." in prompt
    assert consumed_state.status_code == 200
    assert consumed_state.json()["status"] == "consumed"
    assert consumed_state.json()["last_consumed_run_id"] == run_id
