from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.db'}",
        admin_username="admin",
        admin_password="secret",
        admin_token_secret="test-secret-key-with-32-bytes-minimum",
        upload_storage_dir=tmp_path / "uploads",
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/admin/login", json={"username": "admin", "password": "secret"})
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
