"""Per-address rate limits (Go version: internal/httpapi/ratelimit_test.go).

No proxy address is trusted; the client address is X-Real-IP only on a request
whose X-Origin-Secret matches (docs/adr/2026-11-05-vercel-rewrite-and-cloudflare-tunnel.md
D3), and the connection's address
otherwise.
"""

import ipaddress
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from apitest import assert_problem, bare_app, checked_client
from app.http.origin import OriginPolicy
from app.http.ratelimit import RATE_LIMITED, IPLimiter
from widget_schemas import Widget, WidgetPage

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

SECRET = "s" * 43
PROD = OriginPolicy(secrets=(SECRET,), required=True)
DEV = OriginPolicy(secrets=(SECRET,), required=False)
WIDGET = {"name": "gear"}


class Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def __call__(self) -> float:
        return self.now


def limited_client(per_min: int, origin: OriginPolicy) -> TestClient:
    """POST /api/widgets (createWidget) limited to per_min per client address, GET /api/widgets not limited."""
    # a stopped clock: Retry-After does not depend on how fast the machine runs the requests
    app = bare_app(origin=origin, limiter=IPLimiter(per_min, Clock()))

    @app.post("/api/widgets", status_code=201, dependencies=[RATE_LIMITED])
    async def create_widget(body: Widget) -> Widget:
        return body

    @app.get("/api/widgets")
    async def list_widgets() -> WidgetPage:
        return WidgetPage(widgets=[])

    return checked_client(app)


def post_from(client: TestClient, real_ip: str = "", *, secret: str = SECRET) -> int:
    headers = {"x-origin-secret": secret} if secret else {}
    if real_ip:
        headers["x-real-ip"] = real_ip
    return client.post("/api/widgets", json=WIDGET, headers=headers).status_code


def test_rate_limit_per_ip_behind_a_verified_origin() -> None:
    with limited_client(2, PROD) as client:
        for i in range(2):
            assert post_from(client, "198.51.100.1") == 201, f"request {i + 1} from client 1"
        response = client.post(
            "/api/widgets", json=WIDGET, headers={"x-origin-secret": SECRET, "x-real-ip": "198.51.100.1"}
        )
        assert_problem(response, 429, "/problems/rate-limited")
        assert response.headers["retry-after"] == "30"  # one token every 30 s at 2/min
        # a second client behind the same proxy has its own bucket
        assert post_from(client, "198.51.100.2") == 201


def test_rate_limit_ignores_x_real_ip_without_the_secret() -> None:
    # MODE=dev lets a request without the secret through, keyed by the connection's address
    with limited_client(2, DEV) as client:
        post_from(client, "198.51.100.1", secret="")
        post_from(client, "198.51.100.2", secret="")
        # a forged X-Real-IP must not buy a fresh bucket
        assert post_from(client, "198.51.100.3", secret="") == 429


def test_an_unparsable_x_real_ip_falls_back_to_the_connection() -> None:
    with limited_client(1, PROD) as client:
        assert post_from(client, "not-an-address") == 201
        assert post_from(client, "also-not") == 429


def test_a_non_ascii_x_real_ip_falls_back_to_the_connection() -> None:
    with limited_client(1, PROD) as client:
        headers = [(b"x-origin-secret", SECRET.encode()), (b"x-real-ip", b"198.51.100.\xff")]
        assert client.post("/api/widgets", json=WIDGET, headers=headers).status_code == 201
        assert post_from(client, "also-not") == 429


def test_rate_limit_only_named_operations() -> None:
    with limited_client(1, DEV) as client:
        post_from(client, secret="")
        for _ in range(3):
            assert client.get("/api/widgets").status_code == 200


@pytest.mark.parametrize(
    ("content", "content_type", "status"),
    [
        pytest.param('{"name": "x"}', "application/json", 422, id="invalid body"),
        pytest.param('{"name":', "application/json", 400, id="not JSON"),
        pytest.param('{"name": "gear"}', "text/plain", 415, id="wrong media type"),
    ],
)
def test_a_refused_request_uses_a_token(content: str, content_type: str, status: int) -> None:
    # every request that reaches a limited operation counts, the ones its checks refuse too (app.http.ratelimit), so
    # sending invalid requests does not buy more guesses
    with limited_client(1, DEV) as client:
        assert (
            client.post("/api/widgets", content=content, headers={"content-type": content_type}).status_code == status
        )
        assert post_from(client, secret="") == 429


def test_ip_limiter_refills_and_sweeps() -> None:
    clock = Clock()
    start = clock.now
    limiter = IPLimiter(2, clock)
    a, b, c = (ipaddress.ip_address(f"198.51.100.{n}") for n in (1, 2, 3))
    limiter.wait(a)
    limiter.wait(a)
    assert limiter.wait(a) == timedelta(seconds=30)
    clock.now = start + 30
    assert limiter.wait(a) == timedelta(0), "after 30 s a token is back"
    # Sweeps run at most once a refill; this one, at 60 s, finds nothing idle long enough.
    clock.now = start + 60
    limiter.wait(b)
    # The sweep at 120 s: a has been idle over a refill (its bucket is full again), b only 20 s.
    clock.now = start + 100
    limiter.wait(b)
    clock.now = start + 120
    limiter.wait(c)
    assert not limiter.tracks(a)
    assert limiter.tracks(b)


def test_ip_limiter_keys_ipv6_by_64() -> None:
    limiter = IPLimiter(1, Clock())
    limiter.wait(ipaddress.ip_address("2001:db8:1:2::1"))
    assert limiter.wait(ipaddress.ip_address("2001:db8:1:2:ffff::9")) > timedelta(0), "same /64, fresh bucket"
    assert limiter.wait(ipaddress.ip_address("2001:db8:1:3::1")) == timedelta(0), "a different /64 has its own"


def test_ip_limiter_keys_an_ipv4_mapped_address_as_ipv4() -> None:
    limiter = IPLimiter(1, Clock())
    limiter.wait(ipaddress.ip_address("198.51.100.1"))
    assert limiter.wait(ipaddress.ip_address("::ffff:198.51.100.1")) > timedelta(0)
