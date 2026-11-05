"""Accounts and sessions against PostgreSQL (Go version: internal/httpapi/auth_test.go, auth_resources_test.go,
throttle_test.go, internal/modules/auth/service_test.go TestLoginThrottleSchedule, TestCleanup and
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
from authfake import ADMIN_OPERATIONS
from authtest import (
    DEVICE_COOKIE,
    FULL_WIDTH,
    LONG_ENOUGH,
    START,
    AuthAPI,
    FakeBreach,
    auth_api,
    session_token,
    set_cookie,
)

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


def test_create_user_password_too_short_after_nfkc(api: AuthAPI) -> None:
    # 8 times e plus a combining acute: 16 code points as sent, which the contract accepts, and 8 after NFKC (é), the
    # form that is hashed
    password = "e\u0301" * 8
    assert field_errors(api.sign_up("alice", password)) == [("/password", "too_short")]
    assert api.sql("SELECT count(*) FROM users") == [(0,)]


def test_create_user_no_composition_rule(api: AuthAPI) -> None:
    # NIST SP 800-63B-4 forbids composition rules: a long all-lowercase password is fine
    assert api.sign_up("alice", "onlylowercaseletters").status_code == 201


def test_create_user_common_password(api: AuthAPI) -> None:
    # on the list (SecLists Pwdb), 15 characters
    assert field_errors(api.sign_up("alice", "demon1q2w3e4r5t")) == [("/password", "password_too_common")]


def test_create_user_breached_password(postgres: Server) -> None:
    # Go version: auth_test.go TestCreateUserBreachedPassword; the check sees the NFKC form
    breach = FakeBreach(answer=True)
    with auth_api(postgres, breach=breach) as api:
        assert field_errors(api.sign_up("alice", FULL_WIDTH)) == [("/password", "password_too_common")]
        assert breach.checked == ["breached passphrase"]
        # an unreachable breach service does not block sign-ups
        breach.failing = True
        assert api.sign_up("alice").status_code == 201
        assert api.sql("SELECT account FROM users") == [("alice",)]


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


# the session resource (Go version: auth_resources_test.go and auth_test.go TestDeleteAllSessions)


def current_session(api: AuthAPI, token: str) -> dict[str, Any]:
    response = api.do("GET", "/api/sessions/current", token)
    assert response.status_code == 200, response.text
    return response.json()


def list_sessions(api: AuthAPI, token: str, limit: int) -> list[list[dict[str, Any]]]:
    """Every page of the sessions token sees, following next_cursor."""
    pages: list[list[dict[str, Any]]] = []
    cursor = ""
    while True:
        query = f"?limit={limit}" + (f"&cursor={cursor}" if cursor else "")
        response = api.do("GET", "/api/sessions" + query, token)
        assert response.status_code == 200, response.text
        body = response.json()
        pages.append(body["sessions"])
        if body["next_cursor"] is None:
            return pages
        cursor = body["next_cursor"]


def assert_cleared(response: httpx2.Response) -> None:
    assert response.status_code == 204, response.text
    assert [c["max-age"] for c in set_cookie(response)] == ["0"]


def test_list_sessions(api: AuthAPI) -> None:
    alice, first = api.must_sign_up("alice")
    api.must_sign_in("alice")
    second, second_token = api.must_sign_in("alice")
    _, bob = api.must_sign_up("bob")
    pages = list_sessions(api, second_token, 2)
    assert [len(page) for page in pages] == [2, 1]
    listed = [s for page in pages for s in page]
    for s in listed:
        assert s["user"] == alice
        assert s["current"] is (s["id"] == second["id"]), "only the session the request carries is current"
    ids = [s["id"] for s in listed]
    assert ids == sorted(ids, reverse=True)  # newest first
    assert {current_session(api, first)["id"], second["id"]} <= set(ids)
    assert len(set(ids)) == 3
    assert [len(page) for page in list_sessions(api, bob, 20)] == [1]


def test_list_sessions_excludes_expired(api: AuthAPI) -> None:
    _, old = api.must_sign_up("alice")
    stale = current_session(api, old)
    api.clock.advance(timedelta(hours=23))
    fresh, token = api.must_sign_in("alice")
    api.clock.advance(timedelta(hours=2))  # the first session is now 25 hours unused, over the 1-day idle limit
    assert [[s["id"] for s in page] for page in list_sessions(api, token, 20)] == [[fresh["id"]]]
    assert_problem(api.do("DELETE", f"/api/sessions/{stale['id']}", token), 404, "/problems/not-found")


def test_an_invalid_cursor_is_a_validation_error(api: AuthAPI) -> None:
    _, token = api.must_sign_up("alice")
    response = api.do("GET", "/api/sessions?cursor=not-one-of-ours", token)
    assert field_errors(response) == [("/cursor", "invalid")]


def test_delete_session(api: AuthAPI) -> None:
    _, first = api.must_sign_up("alice")
    second, second_token = api.must_sign_in("alice")
    # revoking another of one's sessions leaves the cookie alone
    response = api.do("DELETE", f"/api/sessions/{second['id']}", first)
    assert response.status_code == 204, response.text
    assert "set-cookie" not in response.headers
    assert_me(api, second_token, 401)
    assert_me(api, first, 200)
    # revoking the current one also clears the cookie
    assert_cleared(api.do("DELETE", f"/api/sessions/{current_session(api, first)['id']}", first))
    assert_me(api, first, 401)


def test_another_users_session_cannot_be_revoked(api: AuthAPI) -> None:
    _, alice = api.must_sign_up("alice")
    target = current_session(api, alice)
    _, bob = api.must_sign_up("bob")
    assert_problem(api.do("DELETE", f"/api/sessions/{target['id']}", bob), 404, "/problems/not-found")
    assert_me(api, alice, 200)


def test_delete_all_sessions(api: AuthAPI) -> None:
    _, first = api.must_sign_up("alice")
    _, second = api.must_sign_in("alice")
    _, bob = api.must_sign_up("bob")
    assert_cleared(api.do("DELETE", "/api/sessions", second))
    assert_me(api, first, 401)
    assert_me(api, second, 401)
    assert_me(api, bob, 200)
    assert_problem(api.do("DELETE", "/api/sessions"), 401, "/problems/unauthenticated")


# deleting the account (Go version: auth_resources_test.go TestDeleteCurrentUser, TestDeleteCurrentUserLastAdmin)


def test_delete_current_user(api: AuthAPI) -> None:
    alice, token = api.must_sign_up("alice")
    # the items module's routes land in step 4; its table already references users
    [(item,)] = api.sql("INSERT INTO items (title, owner_id) VALUES ('mine', :owner) RETURNING id", owner=alice["id"])
    _, other = api.must_sign_in("alice")
    api.clock.advance(timedelta(minutes=10, seconds=1))
    refused = api.do("DELETE", "/api/users/me", token)
    assert code(refused, 403, "/problems/forbidden") == "reauthentication_required"
    assert_me(api, token, 200)  # nothing was deleted

    _, fresh = api.must_sign_in("alice")
    assert_cleared(api.do("DELETE", "/api/users/me", fresh))
    assert_me(api, other, 401)
    assert_problem(api.sign_in("alice"), 401, "/problems/unauthenticated")
    assert api.sql("SELECT count(*) FROM items WHERE id = :id", id=item) == [(0,)]
    [event] = api.sql("SELECT action, target_id, actor_id, request_id FROM audit_events")
    assert (event.action, str(event.target_id), str(event.actor_id)) == ("user.deleted", alice["id"], alice["id"])
    assert event.request_id
    api.must_sign_up("alice")  # the name is free again


def test_a_session_issued_ten_minutes_ago_may_still_delete(api: AuthAPI) -> None:
    _, token = api.must_sign_up("alice")
    api.clock.advance(timedelta(minutes=10))
    assert_cleared(api.do("DELETE", "/api/users/me", token))


def test_deleting_the_account_twice_at_once_is_one_deletion(api: AuthAPI) -> None:
    # the second request finds the account gone: its session is gone with it (401), not a 404 the contract lacks
    _, token = api.must_sign_up("alice")

    def delete(_: int) -> int:
        return api.do("DELETE", "/api/users/me", token).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(delete, range(2)))
    assert statuses in ([204, 204], [204, 401])
    assert api.sql("SELECT action FROM audit_events") == [("user.deleted",)]


def test_the_only_enabled_admin_cannot_delete_itself(api: AuthAPI) -> None:
    api.call(api.service.ensure_admin, "root", LONG_ENOUGH)
    _, root = api.must_sign_in("root")
    assert code(api.do("DELETE", "/api/users/me", root), 409, "/problems/conflict") == "last_admin"
    assert_me(api, root, 200)
    # a disabled admin does not count; a second enabled one does
    api.call(api.service.ensure_admin, "spare", LONG_ENOUGH)
    api.sql("UPDATE users SET is_disabled = true WHERE account = 'spare'")
    assert code(api.do("DELETE", "/api/users/me", root), 409, "/problems/conflict") == "last_admin"
    api.sql("UPDATE users SET is_disabled = false WHERE account = 'spare'")
    assert_cleared(api.do("DELETE", "/api/users/me", root))


# administration (Go version: auth_test.go TestAdminDisablesUserRevokesSessions and the next three,
# auth_resources_test.go TestListUsers, TestNonAdminCannotListUsers, TestAdminDeletesUser, authz_test.go
# TestAdminChangesAreAudited)


def must_admin(api: AuthAPI, account: str = "root") -> tuple[str, str]:
    """An enabled admin account and a fresh session of it: (user id, token)."""
    api.call(api.service.ensure_admin, account, LONG_ENOUGH)
    session, token = api.must_sign_in(account)
    return session["user"]["id"], token


def patch_user(api: AuthAPI, token: str, user_id: str, body: object) -> httpx2.Response:
    return api.do(
        "PATCH", f"/api/users/{user_id}", token, json=body, headers={"Content-Type": "application/merge-patch+json"}
    )


def test_an_admin_disables_a_user_and_ends_their_sessions(api: AuthAPI) -> None:
    _, admin = must_admin(api)
    alice, token = api.must_sign_up("alice")
    disabled = patch_user(api, admin, alice["id"], {"is_disabled": True})
    assert (disabled.status_code, disabled.json()["is_disabled"]) == (200, True), disabled.text
    assert_me(api, token, 401)
    enabled = patch_user(api, admin, alice["id"], {"is_disabled": False})
    assert (enabled.status_code, enabled.json()["is_disabled"]) == (200, False), enabled.text
    assert_me(api, token, 401)  # re-enabling does not bring the sessions back
    api.must_sign_in("alice")
    # an empty merge patch changes nothing
    unchanged = patch_user(api, admin, alice["id"], {})
    assert (unchanged.status_code, unchanged.json()["is_disabled"]) == (200, False), unchanged.text
    assert_problem(patch_user(api, admin, str(uuid.uuid4()), {"is_disabled": True}), 404, "/problems/not-found")
    assert_problem(patch_user(api, admin, str(uuid.uuid4()), {}), 404, "/problems/not-found")


def test_the_only_enabled_admin_cannot_be_disabled(api: AuthAPI) -> None:
    root_id, root = must_admin(api)
    assert code(patch_user(api, root, root_id, {"is_disabled": True}), 409, "/problems/conflict") == "last_admin"
    second_id, second = must_admin(api, "second")
    response = patch_user(api, root, root_id, {"is_disabled": True})
    assert response.status_code == 200, response.text
    assert code(patch_user(api, second, second_id, {"is_disabled": True}), 409, "/problems/conflict") == "last_admin"


def test_two_admins_disabling_each_other_at_once_leave_one(api: AuthAPI) -> None:
    # the enabled admins are locked first, so exactly one succeeds; the other gets 409, or 401 when the winner has
    # already ended its session
    root_id, root = must_admin(api)
    second_id, second = must_admin(api, "second")

    def disable(caller_and_target: tuple[str, str]) -> int:
        caller, target = caller_and_target
        return patch_user(api, caller, target, {"is_disabled": True}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(disable, [(root, second_id), (second, root_id)]))
    assert statuses in ([200, 401], [200, 409])
    assert api.sql("SELECT count(*) FROM users WHERE role = 'admin' AND NOT is_disabled") == [(1,)]


def test_a_user_cannot_administer_users(api: AuthAPI) -> None:
    victim, victim_token = api.must_sign_up("alice")
    _, token = api.must_sign_up("mallory")
    refused = [
        patch_user(api, token, victim["id"], {"is_disabled": True}),
        api.do("DELETE", f"/api/users/{victim['id']}", token),
        api.do("GET", "/api/users", token),
    ]
    assert [code(r, 403, "/problems/forbidden") for r in refused] == ["insufficient_role"] * 3
    assert_me(api, victim_token, 200)
    assert_problem(api.do("GET", "/api/users", ""), 401, "/problems/unauthenticated")
    assert api.sql("SELECT count(*) FROM audit_events") == [(0,)]


def test_list_users(api: AuthAPI) -> None:
    admin_id, admin = must_admin(api)
    want = {admin_id: False}
    for account in ("alice", "bob", "carol"):
        user, _ = api.must_sign_up(account)
        want[user["id"]] = account == "bob"
        if account == "bob":
            assert patch_user(api, admin, user["id"], {"is_disabled": True}).status_code == 200
    pages: list[list[dict[str, Any]]] = []
    path = "/api/users?limit=3"
    while True:
        response = api.do("GET", path, admin)
        assert response.status_code == 200, response.text
        pages.append(response.json()["users"])
        cursor = response.json()["next_cursor"]
        if cursor is None:
            break
        path = f"/api/users?limit=3&cursor={cursor}"
    assert [len(p) for p in pages] == [3, 1]
    listed = [u for p in pages for u in p]
    # each user once, disabled ones included, newest (largest UUIDv7) first
    assert {u["id"]: u["is_disabled"] for u in listed} == want
    assert len(listed) == len(want)
    ids = [uuid.UUID(u["id"]) for u in listed]
    assert ids == sorted(ids, reverse=True)


def test_an_admin_deletes_a_user(api: AuthAPI) -> None:
    admin_id, admin = must_admin(api)
    alice, token = api.must_sign_up("alice")
    created = api.do("POST", "/api/items", token, json={"title": "mine"})
    assert created.status_code == 201, created.text
    path = f"/api/users/{alice['id']}"
    assert api.do("DELETE", path, admin).status_code == 204
    assert_me(api, token, 401)
    assert api.sql("SELECT count(*) FROM items") == [(0,)]
    assert_problem(api.do("GET", path, admin), 404, "/problems/not-found")
    assert_problem(api.do("DELETE", path, admin), 404, "/problems/not-found")
    assert code(api.do("DELETE", f"/api/users/{admin_id}", admin), 409, "/problems/conflict") == "last_admin"
    # a stale admin session cannot delete accounts either
    bob, _ = api.must_sign_up("bob")
    api.clock.advance(timedelta(minutes=11))
    stale = api.do("DELETE", f"/api/users/{bob['id']}", admin)
    assert code(stale, 403, "/problems/forbidden") == "reauthentication_required"


# every admin operation that changes state, with the audit action it leaves
AUDITED: dict[tuple[str, str], tuple[str, str]] = {
    ("PATCH", "/users/{id}"): ("user.disabled", '{"is_disabled": true}'),
    ("DELETE", "/users/{id}"): ("user.deleted", ""),
    ("DELETE", "/users/{id}/sign-in-lock"): ("user.unlocked", ""),
}


def test_admin_changes_are_audited(api: AuthAPI) -> None:
    # one audit row naming the admin, the action, the target and the request id (OWASP Logging Cheat Sheet: user
    # administration); a new state-changing admin operation fails here until it has a case, so it cannot ship
    # unaudited
    admin_id, admin = must_admin(api)
    assert set(AUDITED) == {op for op in ADMIN_OPERATIONS if op[0] != "GET"}
    for (method, path), (action, body) in AUDITED.items():
        target, _ = api.must_sign_up(f"target-{method.lower()}")
        before = len(api.sql("SELECT 1 FROM audit_events"))
        headers = {"Content-Type": "application/merge-patch+json"} if body else None
        response = api.do(
            method,
            "/api" + path.replace("{id}", target["id"]),
            admin,
            json=json.loads(body) if body else None,
            headers=headers,
        )
        assert response.status_code < 300, response.text
        events = api.sql("SELECT actor_id, action, target_id, request_id FROM audit_events ORDER BY at, id")
        assert len(events) == before + 1
        event = events[-1]
        assert (str(event.actor_id), event.action, str(event.target_id)) == (admin_id, action, target["id"])
        assert event.request_id


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
    # while waiting even the right password is refused; afterwards it signs in and this client's count starts over
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


def test_signing_in_clears_only_that_clients_wait(api: AuthAPI) -> None:
    # ADR account-abuse-and-lifecycle D4: the owner signing in elsewhere does not lift an attacker's wait
    api.must_sign_up("alice")
    for _ in range(5):
        assert api.sign_in("alice", WRONG, address="198.51.100.1").status_code == 401
    for _ in range(4):
        assert api.sign_in("alice", WRONG, address="198.51.100.2").status_code == 401
    assert api.sign_in("alice", address="198.51.100.2").status_code == 201
    assert code(api.sign_in("alice", address="198.51.100.1"), 429, "/problems/rate-limited") == "login_throttled"
    rows = api.sql("SELECT client_key FROM sign_in_throttles WHERE account_key = 'alice' ORDER BY client_key")
    assert [r.client_key for r in rows] == ["ip:198.51.100.1"]  # the successful client's row is gone


def test_successful_sign_ins_do_not_build_up_a_wait(api: AuthAPI) -> None:
    # each attempt is counted before its password is checked; success must take that count back
    api.must_sign_up("alice")
    for attempt in range(8):
        assert api.sign_in("alice").status_code == 201, f"sign-in {attempt + 1}"


# the account-wide lock and trusted devices (Go version: throttle_test.go; ADR account-abuse-and-lifecycle D4)


def device_cookie(response: httpx2.Response) -> dict[str, str] | None:
    """The device cookie response sets, None without one."""
    found = set_cookie(response, DEVICE_COOKIE)
    assert len(found) <= 1
    return found[0] if found else None


def lock_out(api: AuthAPI, account: str, n: int) -> None:
    """n failed sign-ins for account, each from its own address, so only the account-wide count can stop them."""
    for i in range(n):
        address = f"198.51.{i // 200}.{i % 200 + 1}"
        response = api.sign_in(account, WRONG, address=address)
        assert response.status_code == 401, f"failure {i + 1} = {response.status_code} {response.text}"


def test_untrusted_clients_are_locked_but_a_trusted_device_is_not(api: AuthAPI) -> None:
    # after 100 failures (NIST SP 800-63B 3.2.2) clients the account does not know are refused, but a browser that
    # signed in before still gets in, and its sign-in clears the account-wide count
    api.must_sign_up("alice")
    response = api.sign_in("alice", address="203.0.113.1")
    device = device_cookie(response)
    assert response.status_code == 201
    assert device is not None
    assert {k: v for k, v in device.items() if k != "value"} == {
        "path": "/",
        "max-age": str(180 * 24 * 3600),
        "httponly": "",
        "secure": "",
        "samesite": "Strict",
    }
    lock_out(api, "alice", 99)
    assert api.sign_in("alice", address="192.0.2.10").status_code == 201  # and the count starts over
    lock_out(api, "alice", 100)
    locked = api.sign_in("alice", address="192.0.2.11")
    assert code(locked, 429, "/problems/rate-limited") == "login_throttled"
    assert locked.headers["retry-after"] == "3600"
    trusted = api.sign_in("alice", address="192.0.2.12", device=device["value"])
    assert trusted.status_code == 201, trusted.text
    assert device_cookie(trusted) is None  # already trusted: no second device
    assert api.sign_in("alice", address="192.0.2.13").status_code == 201


def test_a_device_cookie_is_per_account(api: AuthAPI) -> None:
    api.must_sign_up("alice")
    api.must_sign_up("mallory")
    device = device_cookie(api.sign_in("mallory", address="203.0.113.1"))
    assert device is not None
    lock_out(api, "alice", 100)
    assert_problem(api.sign_in("alice", address="203.0.113.1", device=device["value"]), 429, "/problems/rate-limited")


@pytest.mark.parametrize("how", ["sign_out_all", "disable"])
def test_signing_out_everywhere_and_disabling_forget_devices(api: AuthAPI, how: str) -> None:
    user, _ = api.must_sign_up("alice")
    response = api.sign_in("alice", address="203.0.113.1")
    device = device_cookie(response)
    assert device is not None
    if how == "sign_out_all":
        assert api.do("DELETE", "/api/sessions", session_token(response)).status_code == 204
    else:
        _, admin = must_admin(api)
        assert patch_user(api, admin, user["id"], {"is_disabled": True}).status_code == 200
        assert patch_user(api, admin, user["id"], {"is_disabled": False}).status_code == 200
    assert api.sql("SELECT 1 FROM trusted_devices WHERE user_id = :id", id=uuid.UUID(user["id"])) == []
    lock_out(api, "alice", 100)
    assert_problem(api.sign_in("alice", address="203.0.113.1", device=device["value"]), 429, "/problems/rate-limited")


def test_an_admin_sees_and_lifts_the_lock(api: AuthAPI) -> None:
    _, admin = must_admin(api)
    alice, _ = api.must_sign_up("alice")
    path = f"/api/users/{alice['id']}/sign-in-lock"
    assert api.do("GET", path, admin).json() == {"locked": False, "failures": 0}
    lock_out(api, "alice", 100)
    for _ in range(5):
        api.sign_in("alice", WRONG, address="198.51.0.1")  # one address also waits
    # lock_out's first failure was from 198.51.0.1, so its 5th failure comes on the 4th attempt here; the 5th waits
    # and is not counted for the account
    assert api.do("GET", path, admin).json() == {"locked": True, "failures": 104}
    assert api.do("DELETE", path, admin).status_code == 204
    # every count of the account goes, the per-client waits too (ADR account-abuse-and-lifecycle D4)
    assert api.sql("SELECT 1 FROM sign_in_throttles WHERE account_key = 'alice'") == []
    assert api.sign_in("alice", address="198.51.0.1").status_code == 201
    assert api.do("GET", path, admin).json() == {"locked": False, "failures": 0}


def test_the_sign_in_lock_of_a_missing_user_is_not_found(api: AuthAPI) -> None:
    _, admin = must_admin(api)
    path = f"/api/users/{uuid.uuid4()}/sign-in-lock"
    assert_problem(api.do("GET", path, admin), 404, "/problems/not-found")
    assert_problem(api.do("DELETE", path, admin), 404, "/problems/not-found")


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


def test_credentials_never_reach_the_log(postgres: Server, logs: io.StringIO) -> None:
    # passwords and session tokens, at any level, on success and failure paths: sign-up with a failing breach check,
    # wrong and right sign-in, a validation error and a malformed body carrying the password, an unknown token, and
    # sign-out
    with auth_api(postgres, breach=FakeBreach(failing=True)) as api:
        _credentials_never_reach_the_log(api, logs)


def _credentials_never_reach_the_log(api: AuthAPI, logs: io.StringIO) -> None:
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
    assert "breached-password check failed" in out
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
    api.do("GET", "/api/users", token)  # an admin operation
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
        ("authz.denied", alice["id"], ""),
        ("auth.sign_out", alice["id"], ""),
    ]


def test_signing_out_everywhere_is_logged(api: AuthAPI, logs: io.StringIO) -> None:
    alice, token = api.must_sign_up("alice")
    api.do("DELETE", "/api/sessions", token)
    events = [json.loads(line) for line in logs.getvalue().splitlines()]
    [entry] = [e for e in events if e.get("event") == "auth.sign_out_all"]
    assert entry["user_id"] == alice["id"]
    assert entry.get("request_id")


# cleanup (Go version: service_test.go TestCleanup)


def test_cleanup_deletes_expired_sessions_and_stale_throttles(api: AuthAPI) -> None:
    api.must_sign_up("idle")
    api.sign_in("ghost", WRONG)
    api.clock.advance(timedelta(hours=25))
    api.must_sign_up("fresh")
    api.sign_in("ghost2", WRONG)
    counts = api.call(api.service.cleanup)
    # the idle ones; a failed sign-in keeps two rows, its client's and the account-wide count
    assert (counts.sessions, counts.throttles, counts.devices) == (1, 2, 0)
    assert [r.account for r in api.sql("SELECT u.account FROM sessions s JOIN users u ON u.id = s.user_id")] == [
        "fresh"
    ]
    assert [r.account_key for r in api.sql("SELECT account_key FROM sign_in_throttles")] == ["ghost2", "ghost2"]
    # a trusted device lasts 180 days
    assert api.sign_in("fresh").status_code == 201
    api.clock.advance(timedelta(days=179))
    assert api.call(api.service.cleanup).devices == 0
    api.clock.advance(timedelta(days=2))
    assert api.call(api.service.cleanup).devices == 1
