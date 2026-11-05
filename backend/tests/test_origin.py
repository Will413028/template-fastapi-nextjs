"""The origin secret (D3 in
docs/adr/2026-11-05-vercel-rewrite-and-cloudflare-tunnel.md): the backend is reachable through the tunnel by
anyone who knows its address, so an /api request counts as coming through the site only when it carries the
X-Origin-Secret that Vercel's rewrite and the frontend's server-side calls add."""

import pytest

from apitest import assert_problem, checked_client, stub_app
from app.web.origin import OriginPolicy

SECRET = "current-" + "s" * 40
PREVIOUS = "previous-" + "p" * 40
PROD = OriginPolicy(secrets=(SECRET,), required=True)
ROTATING = OriginPolicy(secrets=(SECRET, PREVIOUS), required=True)
# MODE=dev with ORIGIN_SECRET set
DEV = OriginPolicy(secrets=(SECRET,), required=False)


def get_widgets(policy: OriginPolicy, headers: dict[str, str] | None = None, raw: bytes | None = None) -> int:
    sent = [(name.encode(), value.encode()) for name, value in (headers or {}).items()]
    if raw is not None:
        sent.append((b"x-origin-secret", raw))
    with checked_client(stub_app(origin=policy)) as client:
        return client.get("/api/widgets", headers=sent).status_code


def test_prod_refuses_a_request_without_the_secret() -> None:
    with checked_client(stub_app(origin=PROD)) as client:
        response = client.get("/api/widgets")
    body = assert_problem(response, 403, "/problems/forbidden")
    assert "code" not in body  # the contract's code list stays as it is
    assert response.headers["cache-control"] == "no-store"
    assert body["request_id"] == response.headers["x-request-id"]


def test_prod_accepts_the_secret() -> None:
    assert get_widgets(PROD, {"x-origin-secret": SECRET}) == 200


@pytest.mark.parametrize("secret", [SECRET, PREVIOUS])
def test_both_secrets_work_during_a_rotation(secret: str) -> None:
    assert get_widgets(ROTATING, {"x-origin-secret": secret}) == 200


@pytest.mark.parametrize("policy", [PROD, DEV], ids=["prod", "dev"])
@pytest.mark.parametrize("sent", ["", SECRET[:-1], SECRET + "x", SECRET.upper(), f"{SECRET},{SECRET}"])
def test_a_wrong_secret_is_refused(policy: OriginPolicy, sent: str) -> None:
    assert get_widgets(policy, {"x-origin-secret": sent}) == 403


@pytest.mark.parametrize("policy", [PROD, DEV], ids=["prod", "dev"])
def test_a_non_ascii_secret_is_refused(policy: OriginPolicy) -> None:
    assert get_widgets(policy, raw=SECRET[:-1].encode() + "é".encode()) == 403
    assert get_widgets(policy, raw=SECRET[:-1].encode() + b"\xff") == 403


def test_dev_lets_a_request_without_the_header_through() -> None:
    # next dev's rewrite cannot add the header; a header that is sent must still be right
    assert get_widgets(DEV) == 200
    assert get_widgets(OriginPolicy(secrets=(), required=False)) == 200
    assert get_widgets(DEV, {"x-origin-secret": SECRET}) == 200


def test_dev_without_a_secret_refuses_any_secret_sent() -> None:
    assert get_widgets(OriginPolicy(secrets=(), required=False), {"x-origin-secret": SECRET}) == 403


def test_paths_outside_the_api_are_not_checked() -> None:
    # nothing but /api and the loopback-only health checks is served; any other path is the same 404 either way
    with checked_client(stub_app(origin=PROD)) as client:
        response = client.get("/nothing-here")
    assert_problem(response, 404, "/problems/not-found")


def test_prod_needs_a_secret() -> None:
    with pytest.raises(ValueError, match="needs at least one secret"):
        OriginPolicy(secrets=(), required=True)
