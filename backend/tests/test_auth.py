from fastapi.testclient import TestClient


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


def test_protected_routes_require_authentication(client: TestClient) -> None:
    response = client.get("/api/sessions")

    assert response.status_code == 401
