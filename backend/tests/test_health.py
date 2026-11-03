from fastapi.testclient import TestClient

from app.main.factory import create_app


def test_health_reports_ok() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_path_is_not_found() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/nothing-here")
    assert response.status_code == 404
