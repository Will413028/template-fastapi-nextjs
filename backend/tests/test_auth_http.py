"""Authentication at the HTTP layer, without a database (Go version: internal/httpapi/auth_test.go
TestUnauthenticatedBeforeValidation, TestMeIgnoresBearer, TestInvalidSessionCookieCleared; authz_test.go
TestOperationAccessEnforced; config_test.go TestCookieNameFollowsSecure for the cookie the API reads).
"""

import logging
import re
import time
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from apitest import PEER, assert_problem, checked_client
from app.http.origin import OriginPolicy
from app.main import build_openapi
from app.modules.auth.dependencies import SessionCookie
from authfake import ADMIN_OPERATIONS, ALICE, SECURE, SESSION_ID, TOKEN, FakeAuthService, fake_app

SECRET = "s" * 43
SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}


def cookies(response_headers: list[str], name: str) -> list[str]:
    return [h for h in response_headers if h.startswith(f"{name}=")]


def test_a_valid_session_cookie_signs_the_request_in_and_sets_nothing() -> None:
    service = FakeAuthService()
    with checked_client(fake_app(service)) as client:
        response = client.get("/api/users/me", headers={"Cookie": f"__Host-session={TOKEN}"})
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(ALICE.id)
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        pytest.param('{"title": 5}', "application/json", id="invalid body"),
        pytest.param('{"title":', "application/json", id="not JSON"),
        pytest.param('{"title": "a"}', "text/plain", id="wrong media type"),
    ],
)
def test_an_invalid_cookie_with_an_invalid_body_is_unauthenticated_not_invalid(content: str, content_type: str) -> None:
    # access is checked before the body (app.http.route): an anonymous caller learns nothing about the request it
    # should send, and a cookie that is present but dead is anonymous (2b only checked that the cookie was there)
    service = FakeAuthService()
    with checked_client(fake_app(service)) as client:
        for cookie in ({"Cookie": "__Host-session=dead"}, {}):
            headers = SAME_ORIGIN | cookie | {"Content-Type": content_type}
            response = client.post("/api/items", content=content, headers=headers)
            assert_problem(response, 401, "/problems/unauthenticated")


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        pytest.param('{"is_disabled": "yes"}', "application/merge-patch+json", id="invalid body"),
        pytest.param('{"is_disabled":', "application/merge-patch+json", id="not JSON"),
        pytest.param('{"is_disabled": true}', "application/json", id="wrong media type"),
    ],
)
def test_a_user_who_is_not_an_admin_is_refused_before_the_body_is_checked(content: str, content_type: str) -> None:
    # the role is part of access (app.http.route): a non-admin learns nothing about an admin operation's body
    service = FakeAuthService()
    with checked_client(fake_app(service)) as client:
        client.cookies.set("__Host-session", TOKEN)
        response = client.patch(
            f"/api/users/{uuid.uuid4()}", content=content, headers=SAME_ORIGIN | {"Content-Type": content_type}
        )
    assert assert_problem(response, 403, "/problems/forbidden")["code"] == "insufficient_role"


def test_an_invalid_cookie_is_cleared() -> None:
    # HttpOnly: only the server can remove it, and the frontend takes a present cookie for a session
    service = FakeAuthService()
    with checked_client(fake_app(service)) as client:
        response = client.get("/api/users/me", headers={"Cookie": "__Host-session=dead"})
    assert_problem(response, 401, "/problems/unauthenticated")
    assert cookies(response.headers.get_list("set-cookie"), "__Host-session") == [SECURE.clear()]


def test_an_operation_that_sets_the_cookie_is_not_given_a_second_one() -> None:
    service = FakeAuthService()
    with checked_client(fake_app(service)) as client:
        response = client.delete("/api/sessions/current", headers=SAME_ORIGIN | {"Cookie": "__Host-session=dead"})
    assert response.status_code == 204, response.text
    assert cookies(response.headers.get_list("set-cookie"), "__Host-session") == [SECURE.clear()]
    assert service.signed_out == ["dead"]


def test_only_the_cookie_authenticates() -> None:
    service = FakeAuthService()
    with checked_client(fake_app(service)) as client:
        response = client.get("/api/users/me", headers={"Authorization": f"Bearer {TOKEN}"})
    assert_problem(response, 401, "/problems/unauthenticated")


@pytest.mark.parametrize(
    ("cookie", "other"),
    [(SECURE, "session"), (SessionCookie(name="session", device_name="device", secure=False), "__Host-session")],
)
def test_the_cookie_name_follows_cookie_secure(cookie: SessionCookie, other: str) -> None:
    # COOKIE_SECURE=false names the cookie `session` (the __Host- prefix needs Secure); 2b's check knew only the other
    service = FakeAuthService()
    with checked_client(fake_app(service, cookie=cookie)) as client:
        assert client.get("/api/users/me", headers={"Cookie": f"{cookie.name}={TOKEN}"}).status_code == 200
        response = client.get("/api/users/me", headers={"Cookie": f"{other}={TOKEN}"})
        assert_problem(response, 401, "/problems/unauthenticated")


def test_a_request_refused_at_the_edge_never_looks_up_its_session() -> None:
    # the origin guard and the CSRF check read headers only and run before the session lookup
    service = FakeAuthService()
    app = fake_app(service, origin=OriginPolicy(secrets=(SECRET,), required=True))
    # the origin guard's 403 is not a contract response (ADR vercel-rewrite-and-cloudflare-tunnel), so responses are not
    # checked against the contract
    with TestClient(app, client=PEER) as client:
        refused = [
            client.get("/api/users/me", headers={"Cookie": f"__Host-session={TOKEN}", "X-Origin-Secret": "x" * 43}),
            client.post(
                "/api/sessions",
                json={"account": "alice", "password": "pw"},
                headers={
                    "Cookie": f"__Host-session={TOKEN}",
                    "X-Origin-Secret": SECRET,
                    "Sec-Fetch-Site": "cross-site",
                },
            ),
        ]
    assert [r.status_code for r in refused] == [403, 403]
    assert service.lookups == 0


def test_every_operation_needs_a_session_unless_the_contract_says_public() -> None:
    # every operation is called the way an attacker would: its access marker refuses it first
    operations = sorted(
        (method.upper(), path)
        for path, item in build_openapi()["paths"].items()
        for method, operation in item.items()
        if "security" in operation
    )
    assert len(operations) > 3
    service = FakeAuthService()
    with checked_client(fake_app(service)) as client:
        for method, path in operations:
            url = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)
            body: dict[str, object] | None = {} if method in {"POST", "PATCH"} else None
            content_type = "application/merge-patch+json" if method == "PATCH" else "application/json"
            response = client.request(
                method,
                url,
                json=body,
                headers=SAME_ORIGIN | ({"Content-Type": content_type} if body is not None else {}),
            )
            assert_problem(response, 401, "/problems/unauthenticated")


@pytest.mark.parametrize(("method", "path"), ADMIN_OPERATIONS)
def test_a_user_who_is_not_an_admin_is_refused_and_it_is_logged(
    method: str, path: str, caplog: pytest.LogCaptureFixture
) -> None:
    service = FakeAuthService()
    url = "/api" + path.replace("{id}", str(uuid.uuid4()))
    patch = method == "PATCH"
    headers = SAME_ORIGIN | ({"Content-Type": "application/merge-patch+json"} if patch else {})
    with checked_client(fake_app(service)) as client:
        client.cookies.set("__Host-session", TOKEN)
        with caplog.at_level(logging.WARNING, logger="app.http.access"):
            response = client.request(method, url, json={} if patch else None, headers=headers)
    problem = assert_problem(response, 403, "/problems/forbidden")
    assert problem["code"] == "insufficient_role"
    denied = [r for r in caplog.records if getattr(r, "event", None) == "authz.denied"]
    assert [(getattr(r, "user_id", None), getattr(r, "operation", None)) for r in denied] == [
        (str(ALICE.id), f"{method} /api{path}")
    ]


def test_session_cookie_attributes() -> None:
    assert SECURE.set("tok", timedelta(days=7)) == (
        "__Host-session=tok; Path=/; Max-Age=604800; HttpOnly; Secure; SameSite=Lax"
    )
    assert SECURE.clear() == "__Host-session=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"
    assert (
        SessionCookie(name="session", device_name="device", secure=False).clear()
        == "session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
    )


def test_device_cookie_attributes() -> None:
    # SameSite=Strict: only a same-site sign-in reads it (Go version: auth.go Cookie.device)
    assert SECURE.device("dev", timedelta(days=180)) == (
        "__Host-device=dev; Path=/; Max-Age=15552000; HttpOnly; Secure; SameSite=Strict"
    )


def test_the_current_session_is_not_taken_for_a_session_id() -> None:
    # /sessions/current is declared before /sessions/{id}: the other order would route it there and answer 422
    service = FakeAuthService()
    with checked_client(fake_app(service)) as client:
        response = client.get("/api/sessions/current", headers={"Cookie": f"__Host-session={TOKEN}"})
        assert response.status_code == 200, response.text
        assert (response.json()["id"], response.json()["current"]) == (str(SESSION_ID), True)
        assert_problem(client.get("/api/sessions/current"), 401, "/problems/unauthenticated")


def test_sign_out_from_another_site_is_refused() -> None:
    service = FakeAuthService()
    with checked_client(fake_app(service)) as client:
        headers = {"Cookie": f"__Host-session={TOKEN}", "Sec-Fetch-Site": "cross-site"}
        response = client.delete("/api/sessions/current", headers=headers)
    assert assert_problem(response, 403, "/problems/forbidden")["code"] == "cross_origin_request"
    assert service.signed_out == []


def test_serve_cleans_up_on_a_schedule_until_shutdown(caplog: pytest.LogCaptureFixture) -> None:
    # Go version: cmd/app/main.go runEvery(cleanupEvery, Cleanup), plus a run at startup; a failed run is logged and the
    # next one still runs
    service = FakeAuthService()
    service.cleanup_fails = {1}
    deadline = time.monotonic() + 5
    with caplog.at_level(logging.INFO, logger="app.main"), TestClient(fake_app(service, cleanup_every=0.01)):
        while service.cleanups < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
    assert service.cleanups >= 3
    stopped_at = service.cleanups
    time.sleep(0.05)
    assert service.cleanups == stopped_at  # the task ends with the app
    messages = [r.getMessage() for r in caplog.records]
    assert "auth cleanup failed" in messages
    done = [r for r in caplog.records if r.getMessage() == "auth cleanup"]
    assert done
    assert [done[0].__dict__[k] for k in ("sessions_deleted", "throttles_deleted", "devices_deleted")] == [1, 2, 3]
