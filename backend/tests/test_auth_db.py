"""Accounts and sessions against PostgreSQL (Go version: internal/httpapi/auth_test.go, auth_resources_test.go,
throttle_test.go TestSignInThrottlePerClient, internal/modules/auth/service_test.go TestLoginThrottleSchedule and
TestShortIdleLimitKeepsUsedSession). CI only (make test-db). The HTTP behaviour that needs no database is in
test_auth_http.py.
"""

import io
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from apitest import assert_problem
from app.main.logs import configure_logging
from authtest import LONG_ENOUGH, START, AuthAPI, auth_api, session_token, set_cookie

if TYPE_CHECKING:
    from collections.abc import Generator

    import httpx2

    from dbtest import Server

pytestmark = pytest.mark.db

WRONG = "not the password"


@pytest.fixture
def api(postgres: Server) -> Generator[AuthAPI]:
    with auth_api(postgres) as created:
        yield created


def assert_me(api: AuthAPI, token: str, status: int) -> None:
    response = api.me(token)
    assert response.status_code == status, response.text
    if status == 401:
        assert_problem(response, 401, "/problems/unauthenticated")


def code(response: httpx2.Response, status: int, problem_type: str) -> str | None:
    return assert_problem(response, status, problem_type).get("code")


def field_errors(response: httpx2.Response) -> list[tuple[str, str]]:
    return [(e["pointer"], e["code"]) for e in assert_problem(response, 422, "/problems/validation")["errors"]]


def without_request_id(response: httpx2.Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    body.pop("request_id", None)
    return body


# sign-up


def test_create_user_sets_the_session_cookie(api: AuthAPI) -> None:
    response = api.sign_up("Alice")
    assert response.status_code == 201, response.text
    user = response.json()
    assert (user["account"], user["role"], user["is_disabled"]) == ("alice", "user", False)
    assert user["created_at"].endswith("Z")  # RFC 3339 in UTC
    assert response.headers["location"] == f"/api/users/{user['id']}"
    [cookie] = set_cookie(response)
    assert {"httponly", "secure", "path", "samesite"} <= cookie.keys()
    assert "domain" not in cookie
    assert (cookie["path"], cookie["samesite"], cookie["max-age"]) == ("/", "Lax", str(7 * 24 * 3600))
    assert len(cookie["value"]) == 43  # 32 bytes of base64url
    assert_me(api, cookie["value"], 200)


def test_create_user_duplicate(api: AuthAPI) -> None:
    api.must_sign_up("alice")
    assert code(api.sign_up("ALICE"), 409, "/problems/conflict") == "account_taken"


def test_concurrent_sign_ups_for_one_account_get_it_once(api: AuthAPI) -> None:
    # the unique index decides: exactly one gets the account, the rest 409
    def attempt(_: int) -> int:
        return api.sign_up("alice").status_code

    with ThreadPoolExecutor(max_workers=20) as pool:
        codes = sorted(pool.map(attempt, range(20)))
    assert codes == [201] + [409] * 19


def test_create_user_password_too_short(api: AuthAPI) -> None:
    assert field_errors(api.sign_up("alice", "fourteen chars")) == [("/password", "too_short")]


def test_create_user_no_composition_rule(api: AuthAPI) -> None:
    # NIST SP 800-63B-4 forbids composition rules: a long all-lowercase password is fine
    assert api.sign_up("alice", "onlylowercaseletters").status_code == 201


def test_create_user_common_password(api: AuthAPI) -> None:
    # on the list (SecLists Pwdb), 15 characters
    assert field_errors(api.sign_up("alice", "demon1q2w3e4r5t")) == [("/password", "password_too_common")]


# sign-in


def test_create_session(api: AuthAPI) -> None:
    created, _ = api.must_sign_up("alice")
    response = api.sign_in("alice")
    assert response.status_code == 201, response.text
    assert response.json()["user"]["id"] == created["id"]
    assert_me(api, session_token(response), 200)


def test_create_session_returns_the_session(api: AuthAPI) -> None:
    created, _ = api.must_sign_up("alice")
    response = api.sign_in("alice")
    session = response.json()
    assert session["user"] == created
    assert session["current"] is True
    assert uuid.UUID(session["id"]).version == 7
    assert response.headers["location"] == f"/api/sessions/{session['id']}"
    now = START.isoformat().replace("+00:00", "Z")
    expires = (START + timedelta(days=7)).isoformat().replace("+00:00", "Z")
    assert (session["created_at"], session["last_used_at"], session["expires_at"]) == (now, now, expires)


def test_create_session_wrong_password(api: AuthAPI) -> None:
    api.must_sign_up("alice")
    assert code(api.sign_in("alice", WRONG), 401, "/problems/unauthenticated") == "invalid_credentials"


def test_an_unknown_account_costs_a_hash_and_answers_like_a_wrong_password(api: AuthAPI) -> None:
    api.must_sign_up("alice")
    wrong = api.sign_in("alice", WRONG)
    api.hasher.verifies = api.hasher.hashes = 0
    unknown = api.sign_in("bob", WRONG)
    # one verification, against the dummy hash made at startup: the first unknown account costs no extra hash
    assert (api.hasher.verifies, api.hasher.hashes) == (1, 0)
    assert without_request_id(unknown) == without_request_id(wrong)


def test_sign_in_issues_a_new_token_and_ends_the_one_the_request_carried(api: AuthAPI) -> None:
    # no session fixation
    _, old = api.must_sign_up("alice")
    response = api.sign_in("alice", token=old)
    fresh = session_token(response)
    assert fresh != old
    assert_me(api, fresh, 200)
    assert_me(api, old, 401)


def test_sign_in_ignores_the_account_case(api: AuthAPI) -> None:
    api.must_sign_up("alice")
    api.must_sign_in("ALICE")


def test_passwords_are_compared_after_nfkc(api: AuthAPI) -> None:
    full_width = "".join(chr(ord(c) + 0xFEE0) for c in "correct-horse-battery")
    assert api.sign_up("alice", full_width).status_code == 201
    api.must_sign_in("alice", "correct-horse-battery")
    assert api.sign_up("bob", "correct-horse-battery-staple").status_code == 201
    api.must_sign_in("bob", "".join(chr(ord(c) + 0xFEE0) for c in "correct-horse-battery-staple"))


def test_a_disabled_account_signs_in_like_a_wrong_password(api: AuthAPI) -> None:
    api.must_sign_up("alice")
    api.sql("UPDATE users SET is_disabled = true")
    assert code(api.sign_in("alice"), 401, "/problems/unauthenticated") == "invalid_credentials"


# the session


def test_me(api: AuthAPI) -> None:
    created, token = api.must_sign_up("alice")
    response = api.me(token)
    assert response.status_code == 200
    me = response.json()
    assert me["id"] == created["id"]
    assert uuid.UUID(me["id"]).version == 7
    assert (me["account"], me["role"]) == ("alice", "user")


def test_me_without_a_valid_cookie(api: AuthAPI) -> None:
    assert_me(api, "", 401)
    assert_me(api, "A" * 43, 401)


def test_a_dead_cookie_is_cleared_and_a_live_one_left_alone(api: AuthAPI) -> None:
    _, token = api.must_sign_up("alice")
    api.do("DELETE", "/api/sessions/current", token)
    response = api.me(token)
    assert_problem(response, 401, "/problems/unauthenticated")
    assert [c["max-age"] for c in set_cookie(response)] == ["0"]
    # signing in with the dead cookie gets only the new one
    api.must_sign_up("bob")
    [issued] = set_cookie(api.sign_in("bob", token=token))
    assert int(issued["max-age"]) > 0
    assert issued["value"]
    _, live = api.must_sign_up("carol")
    assert "set-cookie" not in api.me(live).headers


def test_me_for_a_disabled_or_deleted_account(api: AuthAPI) -> None:
    _, token = api.must_sign_up("alice")
    api.sql("UPDATE users SET is_disabled = true")  # the session row is still there
    assert_me(api, token, 401)
    _, token = api.must_sign_up("bob")
    api.sql("DELETE FROM users WHERE account = 'bob'")
    assert_me(api, token, 401)


def test_session_idle_expiry(api: AuthAPI) -> None:
    _, token = api.must_sign_up("alice")
    api.clock.advance(timedelta(hours=24) - timedelta(seconds=1))
    assert_me(api, token, 200)  # using it resets the idle limit
    api.clock.advance(timedelta(hours=24) - timedelta(seconds=1))
    assert_me(api, token, 200)
    api.clock.advance(timedelta(hours=24))
    assert_me(api, token, 401)


def test_session_absolute_expiry(api: AuthAPI) -> None:
    _, token = api.must_sign_up("alice")
    for _ in range(13):
        api.clock.advance(timedelta(hours=12))
        assert_me(api, token, 200)
    api.clock.advance(timedelta(hours=12))  # 7 days after sign-up, used every 12 hours
    assert_me(api, token, 401)


def test_a_short_idle_limit_keeps_a_used_session(postgres: Server) -> None:
    # last_used_at is written at most once a minute, but a session in use must not lapse under a shorter idle limit
    with auth_api(postgres, absolute=timedelta(hours=1), idle=timedelta(seconds=30)) as api:
        _, token = api.must_sign_up("alice")
        for _ in range(4):
            api.clock.advance(timedelta(seconds=20))
            assert_me(api, token, 200)


def test_sign_out_clears_the_cookie_with_or_without_a_session(api: AuthAPI) -> None:
    _, token = api.must_sign_up("alice")
    for sent in (token, ""):
        response = api.do("DELETE", "/api/sessions/current", sent)
        assert response.status_code == 204, response.text
        assert [c["max-age"] for c in set_cookie(response)] == ["0"]


def test_sign_out_ends_only_that_session(api: AuthAPI) -> None:
    _, token = api.must_sign_up("alice")
    _, other = api.must_sign_in("alice")
    api.do("DELETE", "/api/sessions/current", token)
    assert_me(api, token, 401)
    assert_me(api, other, 200)


# reading users and sessions


def test_get_user_as_herself_or_an_admin(api: AuthAPI) -> None:
    alice, alice_token = api.must_sign_up("alice")
    api.must_sign_up("root")
    api.sql("UPDATE users SET role = 'admin' WHERE account = 'root'")  # the role is read on every request
    _, admin_token = api.must_sign_in("root")
    for token in (alice_token, admin_token):
        response = api.do("GET", f"/api/users/{alice['id']}", token)
        assert response.status_code == 200, response.text
        assert response.json() == alice


def test_another_users_account_is_not_found_like_a_missing_one(api: AuthAPI) -> None:
    # docs/adr/2026-10-18-api-and-auth-baseline.md D7: ids reveal nothing about other accounts
    alice, _ = api.must_sign_up("alice")
    _, bob = api.must_sign_up("bob")
    other = api.do("GET", f"/api/users/{alice['id']}", bob)
    missing = api.do("GET", f"/api/users/{uuid.uuid7()}", bob)
    assert_problem(other, 404, "/problems/not-found")
    assert without_request_id(other) == without_request_id(missing)
    assert_problem(api.do("GET", f"/api/users/{alice['id']}"), 401, "/problems/unauthenticated")


def test_get_current_session(api: AuthAPI) -> None:
    created, token = api.must_sign_up("alice")
    response = api.do("GET", "/api/sessions/current", token)
    assert response.status_code == 200, response.text
    current = response.json()
    assert current["user"] == created
    assert current["current"] is True
    assert api.do("GET", f"/api/sessions/{current['id']}", token).json() == current
    assert_problem(api.do("GET", "/api/sessions/current"), 401, "/problems/unauthenticated")


def test_get_session(api: AuthAPI) -> None:
    created, token = api.must_sign_up("alice")
    session, _ = api.must_sign_in("alice")
    response = api.do("GET", f"/api/sessions/{session['id']}", token)
    assert response.status_code == 200, response.text
    read = response.json()
    assert read["user"] == created
    assert read["current"] is False  # the request carries the other session
    _, bob = api.must_sign_up("bob")
    assert_problem(api.do("GET", f"/api/sessions/{session['id']}", bob), 404, "/problems/not-found")


def test_an_expired_session_is_not_found_before_cleanup_removes_it(api: AuthAPI) -> None:
    api.must_sign_up("alice")
    stale, _ = api.must_sign_in("alice")
    api.clock.advance(timedelta(hours=23))
    _, token = api.must_sign_in("alice")
    api.clock.advance(timedelta(hours=2))  # the first session is now 25 hours unused, over the 1-day idle limit
    assert_problem(api.do("GET", f"/api/sessions/{stale['id']}", token), 404, "/problems/not-found")

    # the absolute limit too: a session used every 12 hours ends 7 days after sign-in
    api.must_sign_up("bob")
    stale, old = api.must_sign_in("bob")
    for _ in range(13):
        api.clock.advance(timedelta(hours=12))
        assert_me(api, old, 200)
    _, token = api.must_sign_in("bob")
    api.clock.advance(timedelta(hours=13))  # 7 days and 1 hour after the first sign-in, 13 hours after its last use
    assert_problem(api.do("GET", f"/api/sessions/{stale['id']}", token), 404, "/problems/not-found")


# throttling


def test_sign_in_is_throttled_per_account_name_whether_or_not_it_exists(api: AuthAPI) -> None:
    api.must_sign_up("alice")
    throttled: dict[str, dict[str, Any]] = {}
    for account in ("alice", "nobody"):
        for attempt in range(5):
            assert api.sign_in(account, WRONG).status_code == 401, f"{account} attempt {attempt + 1}"
        response = api.sign_in(account, WRONG)
        assert code(response, 429, "/problems/rate-limited") == "login_throttled"
        assert response.headers["retry-after"] == "60"
        throttled[account] = without_request_id(response)
    assert throttled["alice"] == throttled["nobody"]
    # while waiting even the right password is refused; afterwards it signs in and the count starts over
    assert_problem(api.sign_in("alice"), 429, "/problems/rate-limited")
    api.clock.advance(timedelta(minutes=1))
    api.must_sign_in("alice")
    for _ in range(4):
        api.sign_in("alice", WRONG)
    api.must_sign_in("alice")


def test_attempts_sent_at_once_cannot_exceed_the_allowance(api: AuthAPI) -> None:
    # each attempt is counted before its password is checked
    api.must_sign_up("alice")
    api.hasher.delay = 0.05
    api.hasher.verifies = 0

    def attempt(_: int) -> int:
        return api.sign_in("alice", WRONG).status_code

    with ThreadPoolExecutor(max_workers=20) as pool:
        codes = sorted(pool.map(attempt, range(20)))
    assert codes == [401] * 5 + [429] * 15
    assert api.hasher.verifies == 5  # what throttling protects: only 5 guesses were checked


def test_the_wait_doubles_from_the_fifth_failure_up_to_an_hour(api: AuthAPI) -> None:
    # a refused attempt does not count, and each attempt is made once the previous wait has passed
    for failures in range(1, 13):
        assert code(api.sign_in("nobody", WRONG), 401, "/problems/unauthenticated") == "invalid_credentials"
        if failures < 5:
            continue
        wait = min(timedelta(minutes=2 ** (failures - 5)), timedelta(hours=1))
        response = api.sign_in("nobody", WRONG)
        assert code(response, 429, "/problems/rate-limited") == "login_throttled"
        assert response.headers["retry-after"] == str(int(wait.total_seconds())), f"after {failures} failures"
        api.clock.advance(wait)


def test_one_clients_failures_do_not_slow_another(api: AuthAPI) -> None:
    # ADR account-abuse-and-lifecycle D4: a stranger's guesses must not lock the owner out
    api.must_sign_up("alice")
    for _ in range(5):
        assert api.sign_in("alice", WRONG, address="198.51.100.1").status_code == 401
    assert code(api.sign_in("alice", address="198.51.100.1"), 429, "/problems/rate-limited") == "login_throttled"
    assert api.sign_in("alice", address="198.51.100.2").status_code == 201


def test_ipv6_addresses_in_one_64_are_one_client(api: AuthAPI) -> None:
    api.must_sign_up("alice")
    for _ in range(5):
        assert api.sign_in("alice", WRONG, address="2001:db8::1").status_code == 401
    assert code(api.sign_in("alice", address="2001:db8::2"), 429, "/problems/rate-limited") == "login_throttled"
    assert api.sign_in("alice", address="2001:db8:0:1::1").status_code == 201


@pytest.mark.parametrize("operation", ["createUser", "createSession"])
def test_sign_up_and_sign_in_are_limited_per_address(postgres: Server, operation: str) -> None:
    with auth_api(postgres, per_min=2) as api:
        api.call(api.service.create_user, "alice", LONG_ENOUGH)

        def send(i: int) -> httpx2.Response:
            return api.sign_up(f"user{i}") if operation == "createUser" else api.sign_in("alice")

        for i in range(2):
            assert send(i).status_code == 201, f"request {i + 1}"
        response = send(2)
        assert_problem(response, 429, "/problems/rate-limited")
        assert response.headers["retry-after"] == "30"  # one token every 30 s on a stopped clock


# logs


@pytest.fixture
def logs() -> Generator[io.StringIO]:
    """The app's log as JSON lines at debug level; the root logger is restored afterwards."""
    root = logging.getLogger()
    saved = (root.level, list(root.handlers))
    client_log = logging.getLogger("httpx2")
    client_level = client_log.level
    client_log.setLevel(logging.WARNING)  # the test client's own request lines are not the app's
    stream = io.StringIO()
    configure_logging("prod", stream, level=logging.DEBUG)
    yield stream
    root.setLevel(saved[0])
    root.handlers[:] = saved[1]
    client_log.setLevel(client_level)


def test_credentials_never_reach_the_log(api: AuthAPI, logs: io.StringIO) -> None:
    # passwords and session tokens, at any level, on success and failure paths: sign-up, wrong and right sign-in, a
    # validation error and a malformed body carrying the password, an unknown token, and sign-out
    secret_part = uuid.uuid4().hex
    canary = f"canary {secret_part} phrase"
    stray = uuid.uuid4().hex
    sign_up = api.sign_up("canary", canary)
    assert sign_up.status_code == 201, sign_up.text
    api.sign_in("canary", canary + " wrong")
    _, token = api.must_sign_in("canary", canary)
    api.me(token)
    api.sign_up("x", canary)
    malformed = '{"account":"canary","password":"' + canary
    headers = {"Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}
    assert api.client.post("/api/sessions", content=malformed, headers=headers).status_code == 400
    api.me(stray)
    api.do("DELETE", "/api/sessions/current", token)

    out = logs.getvalue()
    assert any(json.loads(line).get("path") == "/api/sessions" for line in out.splitlines()), out
    for what, value in (
        ("password", secret_part),
        ("sign-up session", session_token(sign_up)),
        ("sign-in session", token),
        ("unknown cookie", stray),
    ):
        assert value not in out, f"the log contains the {what}"


def test_security_events_are_logged_with_the_user(api: AuthAPI, logs: io.StringIO) -> None:
    # OWASP Logging Cheat Sheet, ASVS 5.0 16.3: an incident can be traced to an account
    alice, _ = api.must_sign_up("alice")
    api.sign_in("alice", WRONG)
    api.sign_in("nobody", WRONG)
    _, token = api.must_sign_in("alice")
    api.do("DELETE", "/api/sessions/current", token)
    events: list[tuple[str, str, str]] = []
    for line in logs.getvalue().splitlines():
        entry = json.loads(line)
        if "event" in entry:
            assert entry.get("request_id"), f"event {entry['event']} has no request_id"
            events.append((entry["event"], entry.get("user_id", ""), entry.get("reason", "")))
    assert events == [
        ("auth.sign_up", alice["id"], ""),
        ("auth.sign_in_failed", alice["id"], "invalid_credentials"),
        ("auth.sign_in_failed", "", "unknown_account"),
        ("auth.sign_in", alice["id"], ""),
        ("auth.sign_out", alice["id"], ""),
    ]
