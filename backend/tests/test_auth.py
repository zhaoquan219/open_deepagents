from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.models import AdminUserRecord
from app.main import create_app


def test_admin_login_and_identity(client: TestClient) -> None:
    response = client.post("/api/admin/login", json={"username": "admin", "password": "secret"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "bearer"

    me_response = client.get(
        "/api/admin/me",
        headers={"Authorization": f"Bearer {payload['access_token']}"},
    )
    assert me_response.status_code == 200
    assert me_response.json() == {"username": "admin"}

    with client.app.state.database.session_factory() as db:
        admin_user = db.query(AdminUserRecord).filter(AdminUserRecord.username == "admin").first()
        assert admin_user is not None
        assert admin_user.email == "admin@example.com"
        assert admin_user.last_login_at is not None


def test_protected_routes_require_authentication(client: TestClient) -> None:
    response = client.get("/api/sessions")

    assert response.status_code == 401


def test_admin_auth_can_be_disabled_for_local_chat_access(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'no-auth.db'}",
        admin_email="admin@example.com",
        admin_username="admin",
        admin_password="secret",
        admin_token_secret="test-secret-key-with-32-bytes-minimum",
        upload_storage_dir=tmp_path / "uploads",
        admin_auth_enabled=False,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        me_response = client.get("/api/admin/me")
        sessions_response = client.get("/api/sessions")

    assert me_response.status_code == 200
    assert me_response.json() == {"username": "admin"}
    assert sessions_response.status_code == 200
