from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_session_message_and_upload_crud(client: TestClient, auth_headers: dict[str, str]) -> None:
    create_session = client.post("/api/sessions", headers=auth_headers, json={"title": "Demo"})
    assert create_session.status_code == 201
    session_id = create_session.json()["id"]

    list_sessions = client.get("/api/sessions", headers=auth_headers)
    assert list_sessions.status_code == 200
    assert len(list_sessions.json()) == 1

    create_message = client.post(
        f"/api/sessions/{session_id}/messages",
        headers=auth_headers,
        json={"role": "user", "content": "hello backend", "run_id": "run-1"},
    )
    assert create_message.status_code == 201
    message_id = create_message.json()["id"]

    list_messages = client.get(f"/api/sessions/{session_id}/messages", headers=auth_headers)
    assert list_messages.status_code == 200
    assert list_messages.json()[0]["content"] == "hello backend"

    upload_response = client.post(
        f"/api/sessions/{session_id}/uploads",
        headers=auth_headers,
        data={"message_id": message_id},
        files={"file": ("note.txt", b"backend attachment", "text/plain")},
    )
    assert upload_response.status_code == 201
    upload_payload = upload_response.json()

    session_detail = client.get(f"/api/sessions/{session_id}", headers=auth_headers)
    assert session_detail.status_code == 200
    session_payload = session_detail.json()
    assert session_payload["last_run_id"] == "run-1"
    assert len(session_payload["messages"]) == 1
    assert len(session_payload["uploads"]) == 1

    upload_content = client.get(
        f"/api/uploads/{upload_payload['id']}/content",
        headers=auth_headers,
    )
    assert upload_content.status_code == 200
    assert upload_content.content == b"backend attachment"

    update_message = client.patch(
        f"/api/messages/{message_id}",
        headers=auth_headers,
        json={"content": "updated backend"},
    )
    assert update_message.status_code == 200
    assert update_message.json()["content"] == "updated backend"

    update_session = client.patch(
        f"/api/sessions/{session_id}",
        headers=auth_headers,
        json={"title": "Renamed"},
    )
    assert update_session.status_code == 200
    assert update_session.json()["title"] == "Renamed"

    delete_message = client.delete(f"/api/messages/{message_id}", headers=auth_headers)
    assert delete_message.status_code == 204

    delete_session = client.delete(f"/api/sessions/{session_id}", headers=auth_headers)
    assert delete_session.status_code == 204

    final_sessions = client.get("/api/sessions", headers=auth_headers)
    assert final_sessions.status_code == 200
    assert final_sessions.json() == []


def test_list_sessions_backfills_placeholder_title_from_first_user_message(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    create_session = client.post("/api/sessions", headers=auth_headers, json={})
    assert create_session.status_code == 201
    session_id = create_session.json()["id"]

    create_message = client.post(
        f"/api/sessions/{session_id}/messages",
        headers=auth_headers,
        json={
            "role": "user",
            "content": "整理本周发布前需要确认的接口回归项\n第二行不应进入标题",
        },
    )
    assert create_message.status_code == 201

    list_sessions = client.get("/api/sessions", headers=auth_headers)
    assert list_sessions.status_code == 200
    assert list_sessions.json()[0]["title"] == "整理本周发布前需要确认的接口回归项"

    session_detail = client.get(f"/api/sessions/{session_id}", headers=auth_headers)
    assert session_detail.status_code == 200
    assert session_detail.json()["title"] == "整理本周发布前需要确认的接口回归项"


def test_upload_hook_can_enrich_upload_metadata(tmp_path) -> None:
    hook_module = tmp_path / "upload_hooks.py"
    hook_module.write_text(
        "\n".join(
            [
                "def enrich(context):",
                "    return {",
                "        'hooked': True,",
                "        'payload_size': len(context.payload),",
                "        'upload_path': context.upload_path,",
                "    }",
            ]
        ),
        encoding="utf-8",
    )
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'upload-hook.db'}",
        admin_email="admin@example.com",
        admin_username="admin",
        admin_password="secret",
        admin_token_secret="test-secret-key-with-32-bytes-minimum",
        upload_storage_dir=tmp_path / "uploads",
        deepagents_upload_hook_specs=f"{hook_module}:enrich",
    )
    app = create_app(settings)

    with TestClient(app) as client:
        login = client.post("/api/admin/login", json={"username": "admin", "password": "secret"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        create_session = client.post("/api/sessions", headers=headers, json={"title": "Demo"})
        session_id = create_session.json()["id"]

        upload_response = client.post(
            f"/api/sessions/{session_id}/uploads",
            headers=headers,
            files={"file": ("note.txt", b"backend attachment", "text/plain")},
        )

    assert upload_response.status_code == 201
    extra = upload_response.json()["extra"]
    assert extra["hooked"] is True
    assert extra["payload_size"] == len(b"backend attachment")
    assert extra["upload_path"].endswith(upload_response.json()["storage_key"])
