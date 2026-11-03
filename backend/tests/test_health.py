import time

from fastapi.testclient import TestClient

from apitest import FakeDatabase, contract
from app.main.factory import create_app


def test_health_reports_ok_even_when_the_database_is_down() -> None:
    with TestClient(create_app(contract=contract(), database=FakeDatabase(up=False))) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_the_database_answers() -> None:
    with TestClient(create_app(contract=contract(), database=FakeDatabase())) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_not_ready_is_an_unavailable_problem() -> None:
    with TestClient(create_app(contract=contract(), database=FakeDatabase(up=False))) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body.pop("request_id") == response.headers["x-request-id"]
    assert body == {"type": "/problems/unavailable", "title": "Service unavailable", "status": 503}


def test_a_ping_that_hangs_is_not_ready_within_the_healthcheck_timeout() -> None:
    # a pool exhausted by real requests must answer 503 before app healthcheck's 2 s client timeout, not hang
    with TestClient(create_app(contract=contract(), database=FakeDatabase(hangs=True))) as client:
        started = time.monotonic()
        response = client.get("/health/ready")
        elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert elapsed < 2


def test_shutdown_closes_the_pool() -> None:
    database = FakeDatabase()
    with TestClient(create_app(database, contract=contract())):
        assert not database.closed
    assert database.closed


def test_unknown_path_is_not_found() -> None:
    with TestClient(create_app(contract=contract(), database=FakeDatabase())) as client:
        response = client.get("/nothing-here")
    assert response.status_code == 404
