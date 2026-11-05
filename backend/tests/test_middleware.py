"""Request ids and security headers (Go version: internal/httpapi/middleware_test.go).

Unlike the Go version, no X-Request-ID from outside is believed: there is no trusted proxy that sets one
(ADR vercel-rewrite-and-cloudflare-tunnel D4), so the backend always generates it.
"""

import uuid

import pytest

from apitest import checked_client, stub_app

SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "cache-control": "no-store",
    "referrer-policy": "no-referrer",
    "content-security-policy": "frame-ancestors 'none'",
    "x-frame-options": "DENY",
}


def assert_generated(request_id: str) -> None:
    assert uuid.UUID(request_id).version == 7


@pytest.mark.parametrize("sent", ["abc-123", "a" * 64, 'x" level=ERROR msg="forged'])
def test_request_id_from_the_client_is_replaced(sent: str) -> None:
    with checked_client(stub_app()) as client:
        response = client.get("/api/widgets", headers={"x-request-id": sent})
    assert response.headers["x-request-id"] != sent
    assert_generated(response.headers["x-request-id"])


def test_each_request_gets_its_own_id() -> None:
    with checked_client(stub_app()) as client:
        ids = {client.get(path).headers["x-request-id"] for path in ("/health", "/api/widgets", "/api/nope")}
    assert len(ids) == 3
    for request_id in ids:
        assert_generated(request_id)


@pytest.mark.parametrize("path", ["/api/nope", "/api/fail", "/nothing-here"])
def test_problem_carries_the_request_id(path: str) -> None:
    with checked_client(stub_app()) as client:
        response = client.get(path)
    assert response.json()["request_id"] == response.headers["x-request-id"]


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/api/widgets"), ("GET", "/api/nope"), ("GET", "/api/fail"), ("GET", "/health"), ("PUT", "/health")],
)
def test_security_headers_on_every_response(method: str, path: str) -> None:
    with checked_client(stub_app()) as client:
        response = client.request(method, path)
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get(name) == value, name
    # these do nothing for JSON, and X-XSS-Protection enables an XS-Leak
    assert "x-xss-protection" not in response.headers
    assert "permissions-policy" not in response.headers
