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

from apitest import PEER, assert_problem, checked_client, contract
from app.main.routes import contract_operations
from app.modules.auth.dependencies import SessionCookie
from app.web.origin import OriginPolicy
from authfake import ALICE, SECURE, SESSION_ID, TOKEN, FakeAuthService, fake_app

SECRET = "s" * 43
SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}


def cookies(response_headers: list[str], name: str) -> list[str]:
    return [h for h in response_headers if h.startswith(f"{name}=")]


def test_a_valid_session_cookie_signs_the_request_in_and_sets_nothing() -> None:
    service = FakeAuthService()
    with checked_client(fake_app(service), against=contract()) as client:
        response = client.get("/api/users/me", headers={"Cookie": f"__Host-session={TOKEN}"})
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(ALICE.id)
    assert "set-cookie" not in response.headers


def test_an_invalid_cookie_with_an_invalid_body_is_unauthenticated_not_invalid() -> None:
    # security is checked before the body: an anonymous caller learns nothing about the request it should send, and a
    # cookie that is present but dead is anonymous (2b only checked that the cookie was there)
    service = FakeAuthService()
    with checked_client(fake_app(service), against=contract()) as client:
        for cookie in ({"Cookie": "__Host-session=dead"}, {}):
            response = client.post("/api/items", json={"title": 5}, headers=SAME_ORIGIN | cookie)
            assert_problem(response, 401, "/problems/unauthenticated")


def test_an_invalid_cookie_is_cleared() -> None:
    # HttpOnly: only the server can remove it, and the frontend takes a present cookie for a session
    service = FakeAuthService()
    with checked_client(fake_app(service), against=contract()) as client:
        response = client.get("/api/users/me", headers={"Cookie": "__Host-session=dead"})
    assert_problem(response, 401, "/problems/unauthenticated")
    assert cookies(response.headers.get_list("set-cookie"), "__Host-session") == [SECURE.clear()]


def test_an_operation_that_sets_the_cookie_is_not_given_a_second_one() -> None:
    service = FakeAuthService()
    with checked_client(fake_app(service), against=contract()) as client:
        response = client.delete("/api/sessions/current", headers=SAME_ORIGIN | {"Cookie": "__Host-session=dead"})
    assert response.status_code == 204, response.text
    assert cookies(response.headers.get_list("set-cookie"), "__Host-session") == [SECURE.clear()]
    assert service.signed_out == ["dead"]


def test_only_the_cookie_authenticates() -> None:
    service = FakeAuthService()
    with checked_client(fake_app(service), against=contract()) as client:
        response = client.get("/api/users/me", headers={"Authorization": f"Bearer {TOKEN}"})
    assert_problem(response, 401, "/problems/unauthenticated")


@pytest.mark.parametrize(
    ("cookie", "other"),
    [(SECURE, "session"), (SessionCookie(name="session", secure=False), "__Host-session")],
)
def test_the_cookie_name_follows_cookie_secure(cookie: SessionCookie, other: str) -> None:
    # COOKIE_SECURE=false names the cookie `session` (the __Host- prefix needs Secure); 2b's check knew only the other
    service = FakeAuthService()
    with checked_client(fake_app(service, cookie=cookie), against=contract()) as client:
        assert client.get("/api/users/me", headers={"Cookie": f"{cookie.name}={TOKEN}"}).status_code == 200
        response = client.get("/api/users/me", headers={"Cookie": f"{other}={TOKEN}"})
        assert_problem(response, 401, "/problems/unauthenticated")


def test_a_request_refused_at_the_edge_never_looks_up_its_session() -> None:
    # the origin guard and the CSRF check read headers only and run before the session lookup
    service = FakeAuthService()
    app = fake_app(service, origin=OriginPolicy(secrets=(SECRET,), required=True))
    # the origin guard's 403 is not a contract response (D6), so responses are not checked against the contract
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
    # every operation is called the way an attacker would, routed or not: the contract's security refuses it first
    service = FakeAuthService()
    with checked_client(fake_app(service), against=contract()) as client:
        for (method, path), public in sorted(contract_operations(contract()).items()):
            if public:
                continue
            url = "/api" + re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)
            body: dict[str, object] | None = {} if method in {"POST", "PATCH"} else None
            content_type = "application/merge-patch+json" if method == "PATCH" else "application/json"
            response = client.request(
                method,
                url,
                json=body,
                headers=SAME_ORIGIN | ({"Content-Type": content_type} if body is not None else {}),
            )
            assert_problem(response, 401, "/problems/unauthenticated")


def test_session_cookie_attributes() -> None:
    assert SECURE.set("tok", timedelta(days=7)) == (
        "__Host-session=tok; Path=/; Max-Age=604800; HttpOnly; Secure; SameSite=Lax"
    )
    assert SECURE.clear() == "__Host-session=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"
    assert SessionCookie(name="session", secure=False).clear() == "session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"


def test_the_current_session_is_not_taken_for_a_session_id() -> None:
    # /sessions/current is declared before /sessions/{id}: the other order would route it there and answer 422
    service = FakeAuthService()
    with checked_client(fake_app(service), against=contract()) as client:
        response = client.get("/api/sessions/current", headers={"Cookie": f"__Host-session={TOKEN}"})
        assert response.status_code == 200, response.text
        assert (response.json()["id"], response.json()["current"]) == (str(SESSION_ID), True)
        assert_problem(client.get("/api/sessions/current"), 401, "/problems/unauthenticated")


def test_sign_out_from_another_site_is_refused() -> None:
    service = FakeAuthService()
    with checked_client(fake_app(service), against=contract()) as client:
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
    with caplog.at_level(logging.INFO, logger="app.main.factory"), TestClient(fake_app(service, cleanup_every=0.01)):
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
    assert (done[0].__dict__["sessions_deleted"], done[0].__dict__["throttles_deleted"]) == (1, 2)
