import asyncio
import time

from fastapi.testclient import TestClient

from app.main.factory import create_app


class FakeDatabase:
    def __init__(self, *, up: bool = True, hangs: bool = False) -> None:
        self.up = up
        self.hangs = hangs
        self.closed = False

    async def ping(self) -> None:
        if self.hangs:
            await asyncio.Event().wait()
        if not self.up:
            msg = "connection refused"
            raise OSError(msg)

    async def close(self) -> None:
        self.closed = True


def test_health_reports_ok_even_when_the_database_is_down() -> None:
    with TestClient(create_app(FakeDatabase(up=False))) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_the_database_answers() -> None:
    with TestClient(create_app(FakeDatabase())) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_not_ready_is_an_unavailable_problem() -> None:
    with TestClient(create_app(FakeDatabase(up=False))) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {"type": "/problems/unavailable", "title": "Service unavailable", "status": 503}


def test_a_ping_that_hangs_is_not_ready_within_the_healthcheck_timeout() -> None:
    # a pool exhausted by real requests must answer 503 before app healthcheck's 2 s client timeout, not hang
    with TestClient(create_app(FakeDatabase(hangs=True))) as client:
        started = time.monotonic()
        response = client.get("/health/ready")
        elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert elapsed < 2


def test_shutdown_closes_the_pool() -> None:
    database = FakeDatabase()
    with TestClient(create_app(database)):
        assert not database.closed
    assert database.closed


def test_unknown_path_is_not_found() -> None:
    with TestClient(create_app(FakeDatabase())) as client:
        response = client.get("/nothing-here")
    assert response.status_code == 404
