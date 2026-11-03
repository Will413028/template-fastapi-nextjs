import dataclasses
import time

import pytest
from fastapi.testclient import TestClient

from apitest import LOOPBACK, PEER, FakeDatabase, assert_problem, contract, deps
from app.main.factory import create_app
from app.web.origin import OriginPolicy

PROD = OriginPolicy(secrets=("s" * 43,), required=True)


def health_client(database: FakeDatabase, *, peer: tuple[str, int] = LOOPBACK) -> TestClient:
    """The real contract's app; health checks come from the container itself (app healthcheck), so from loopback."""
    return TestClient(create_app(dataclasses.replace(deps(contract()), database=database)), client=peer)


def test_health_reports_ok_even_when_the_database_is_down() -> None:
    with health_client(FakeDatabase(up=False)) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_the_database_answers() -> None:
    with health_client(FakeDatabase()) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_not_ready_is_an_unavailable_problem() -> None:
    with health_client(FakeDatabase(up=False)) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body.pop("request_id") == response.headers["x-request-id"]
    assert body == {"type": "/problems/unavailable", "title": "Service unavailable", "status": 503}


def test_a_ping_that_hangs_is_not_ready_within_the_healthcheck_timeout() -> None:
    # a pool exhausted by real requests must answer 503 before app healthcheck's 2 s client timeout, not hang
    with health_client(FakeDatabase(hangs=True)) as client:
        started = time.monotonic()
        response = client.get("/health/ready")
        elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert elapsed < 2


def test_shutdown_closes_the_pool() -> None:
    database = FakeDatabase()
    with health_client(database):
        assert not database.closed
    assert database.closed


def test_unknown_path_is_not_found() -> None:
    with health_client(FakeDatabase()) as client:
        response = client.get("/nothing-here")
    assert response.status_code == 404


@pytest.mark.parametrize("path", ["/health", "/health/ready"])
@pytest.mark.parametrize("peer", [PEER, ("10.0.0.3", 40000), ("2001:db8::1", 40000), ("testclient", 50000)])
def test_health_is_not_found_from_anywhere_but_loopback(path: str, peer: tuple[str, int]) -> None:
    # The tunnel forwards only /api/*, but the backend does not rely on that: a probe from cloudflared's container or
    # any other address finds nothing.
    with health_client(FakeDatabase(), peer=peer) as client:
        response = client.get(path)
    assert_problem(response, 404, "/problems/not-found")


@pytest.mark.parametrize("peer", [LOOPBACK, ("::1", 50000), ("::ffff:127.0.0.1", 50000)])
def test_health_needs_no_origin_secret_from_loopback(peer: tuple[str, int]) -> None:
    app = create_app(dataclasses.replace(deps(contract()), origin=PROD))
    with TestClient(app, client=peer) as client:
        response = client.get("/health")
    assert response.status_code == 200
