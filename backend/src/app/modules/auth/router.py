"""The auth operations: accounts (/users) and sessions (/sessions) (Go version: internal/httpapi/users.go). Each
route's summary, docstring and responses are its operation in the contract, and its bodies are the models in
schemas.py (app.main.build_openapi); notes for developers are comments.

A 201 carries a Location whose GET answers 200 for the creator (docs/adr/2026-10-18-api-and-auth-baseline.md D9).
Sign-up and sign-in are limited per client address (RATE_LIMITED, ADR api-and-auth-baseline D6); sign-in is also
throttled per account and client in the service.
"""

import uuid
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Body, Response, status
from pydantic import MISSING

from app.core.errors import AppError, Kind
from app.core.pagination import DEFAULT_LIMIT
from app.core.principal import session_invalid
from app.http.access import PUBLIC, Admin, SignedIn
from app.http.origin import current_client
from app.http.ratelimit import RATE_LIMITED, limit_key
from app.http.request_id import current_request_id
from app.http.schemas import (
    CLEAR_SESSION_COOKIE,
    LOCATION,
    MERGE_PATCH,
    SET_SESSION_COOKIE,
    Cursor,
    Limit,
    headers,
    problems,
)
from app.modules.auth import schemas as api
from app.modules.auth.dependencies import Current, CurrentAdmin, CurrentSession, Service

if TYPE_CHECKING:
    from app.modules.auth.service import SessionInfo, User

# Routes match in the order they are declared: a literal path (/users/me, /sessions/current) comes before the
# template that would also match it (/users/{id}, /sessions/{id}), or that template's handler gets it.
router = APIRouter(prefix="/api", tags=["auth"])


def _user(user: User) -> api.User:
    return api.User(
        id=user.id, account=user.account, role=user.role, is_disabled=user.is_disabled, created_at=user.created_at
    )


def _session(session: SessionInfo, user: User, current: uuid.UUID | None) -> api.Session:
    return api.Session(
        id=session.id,
        user=_user(user),
        created_at=session.created_at,
        last_used_at=session.last_used_at,
        expires_at=session.expires_at,
        current=session.id == current,
    )


def _no_content(clear_cookie: str | None = None) -> Response:
    """A 204, clearing the session cookie when clear_cookie is its Set-Cookie value."""
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    if clear_cookie is not None:
        response.headers.append("Set-Cookie", clear_cookie)
    return response


def _issued(response: Response, location: str, cookie: str) -> None:
    response.headers["Location"] = location
    response.headers.append("Set-Cookie", cookie)


def _address_key() -> str:
    """Where a sign-in comes from, for its throttle: the address the rate limit uses (IPv4, or the IPv6 /64). The
    connection always has one over TCP; without one every client would share the "unknown" bucket."""
    client = current_client()
    address = limit_key(client.address if client else None)
    return f"ip:{address if address is not None else 'unknown'}"


@router.post(
    "/users",
    status_code=status.HTTP_201_CREATED,
    dependencies=[PUBLIC, RATE_LIMITED],
    operation_id="createUser",
    summary="Create an account and sign in to it.",
    response_description="The account was created and signed in.",
    responses=problems(400, 403, 409, 413, 415, 422, 429, 500) | headers(201, LOCATION, SET_SESSION_COOKIE),
)
async def create_user(
    body: api.CreateUserRequest, service: Service, session: CurrentSession, response: Response
) -> api.User:
    """Creates a `user` account and a session; the session cookie is set on the response."""
    user, new = await service.create_user(body.account, body.password)
    _issued(response, f"/api/users/{user.id}", session.cookie.set(new.token, new.max_age))
    return _user(user)


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    dependencies=[PUBLIC, RATE_LIMITED],
    operation_id="createSession",
    summary="Sign in.",
    response_description="Signed in; `Location` is the new session.",
    responses=problems(400, 401, 403, 413, 415, 422, 429, 500) | headers(201, LOCATION, SET_SESSION_COOKIE),
)
async def create_session(
    body: api.CreateSessionRequest, service: Service, session: CurrentSession, response: Response
) -> api.Session:
    """Always issues a new session and ends the one the request carried, if any. A wrong password, an unknown
    account and a disabled account get the same 401. Throttling (429), whether or not the account exists: each
    client (address, or a browser that signed in to the account before) waits after 5 failures in a row, and
    after 100 failures from clients the account does not know, only such browsers may try until a sign-in
    succeeds or an admin unlocks it. A sign-in from a browser that has no device cookie for the account also sets
    one (a second Set-Cookie, `__Host-device`, HttpOnly, SameSite=Strict, 180 days)."""
    signed_in = await service.sign_in(
        body.account,
        body.password,
        previous_token=session.token,
        address_key=_address_key(),
        device_token=session.device_token,
    )
    new = signed_in.session
    _issued(response, f"/api/sessions/{new.info.id}", session.cookie.set(new.token, new.max_age))
    if signed_in.device is not None:
        response.headers.append("Set-Cookie", session.cookie.device(signed_in.device.token, signed_in.device.max_age))
    return _session(new.info, signed_in.user, new.info.id)


@router.delete(
    "/sessions/current",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[PUBLIC],
    operation_id="deleteCurrentSession",
    summary="Sign out.",
    response_description="Signed out.",
    responses=problems(403, 500) | headers(204, CLEAR_SESSION_COOKIE),
)
async def delete_current_session(service: Service, session: CurrentSession) -> Response:
    """Ends the session the request carries and clears the cookie. Succeeds without a valid session too."""
    authenticated = session.authenticated
    await service.sign_out(session.token, user_id=authenticated.user.id if authenticated else None)
    return _no_content(session.cookie.clear())


@router.get(
    "/sessions",
    operation_id="listSessions",
    summary="The signed-in user's sessions, newest first.",
    response_description="One page of sessions.",
    responses=problems(401, 422, 500),
)
async def list_sessions(
    service: Service, current: Current, limit: Limit = DEFAULT_LIMIT, cursor: Cursor = None
) -> api.SessionPage:
    """Only sessions that are still valid (within both the absolute and the idle limit). Keyset pagination; pass
    `next_cursor` back as `cursor` until it is null."""
    # the contract has already checked limit's range (app.http.contract)
    page = await service.sessions(current.principal, limit=limit, cursor=cursor)
    return api.SessionPage(
        sessions=[_session(s, current.user, current.session_id) for s in page.sessions], next_cursor=page.next_cursor
    )


@router.delete(
    "/sessions",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteAllSessions",
    summary="Sign out everywhere.",
    response_description="Signed out everywhere.",
    responses=problems(401, 403, 500) | headers(204, CLEAR_SESSION_COOKIE),
)
async def delete_all_sessions(service: Service, current: Current, session: CurrentSession) -> Response:
    """Ends every session of the signed-in user, this one included, and clears the cookie."""
    await service.sign_out_all(current.user.id)
    return _no_content(session.cookie.clear())


@router.delete(
    "/users/me",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteCurrentUser",
    summary="Delete the signed-in user's account.",
    response_description="Deleted.",
    responses=problems(401, 403, 409, 500) | headers(204, CLEAR_SESSION_COOKIE),
)
async def delete_current_user(service: Service, current: Current, session: CurrentSession) -> Response:
    """Deletes the account and everything it owns, ends all its sessions and clears the cookie. The session must
    have signed in within the last 10 minutes: sign in again (POST /sessions) first, or the answer is 403
    `reauthentication_required`. The only enabled admin cannot delete itself (409 `last_admin`)."""
    await service.delete_own_account(current.principal, current.session_id, request_id=current_request_id())
    return _no_content(session.cookie.clear())


@router.get(
    "/users/me",
    operation_id="getCurrentUser",
    summary="The signed-in user.",
    response_description="The signed-in user.",
    responses=problems(401, 500),
)
async def get_current_user(current: Current) -> api.User:
    return _user(current.user)


@router.get(
    "/sessions/current",
    operation_id="getCurrentSession",
    summary="The session the request carries.",
    response_description="The current session.",
    responses=problems(401, 500),
)
async def get_current_session(service: Service, current: Current) -> api.Session:
    # getSession for the session the request carries, as /users/me is for the user
    try:
        session = await service.session(current.principal, current.session_id)
    except AppError as err:
        if err.kind is not Kind.NOT_FOUND:
            raise
        # revoked or expired since the middleware read it: the request no longer has a session
        raise session_invalid() from err
    return _session(session, current.user, current.session_id)


@router.get(
    "/users",
    operation_id="listUsers",
    summary="All users, newest first (admin only).",
    response_description="One page of users.",
    responses=problems(401, 403, 422, 500),
)
async def list_users(
    service: Service, _admin: Admin, limit: Limit = DEFAULT_LIMIT, cursor: Cursor = None
) -> api.UserPage:
    """Disabled users included. Keyset pagination; pass `next_cursor` back as `cursor` until it is null."""
    # the contract has already checked limit's range (app.http.contract)
    page = await service.list_users(limit=limit, cursor=cursor)
    return api.UserPage(users=[_user(u) for u in page.users], next_cursor=page.next_cursor)


@router.get(
    "/users/{id}/sign-in-lock",
    operation_id="getSignInLock",
    summary="Whether the user's account refuses sign-ins from unknown clients (admin only).",
    response_description="The lock state.",
    responses=problems(401, 403, 404, 422, 500),
)
async def get_sign_in_lock(id: uuid.UUID, service: Service, _admin: Admin) -> api.SignInLock:
    lock = await service.sign_in_lock(id)
    return api.SignInLock(locked=lock.locked, failures=lock.failures)


@router.delete(
    "/users/{id}/sign-in-lock",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteSignInLock",
    summary="Unlock the user's sign-in (admin only).",
    response_description="Unlocked.",
    responses=problems(401, 403, 404, 422, 500),
)
async def delete_sign_in_lock(id: uuid.UUID, service: Service, principal: Admin) -> Response:
    """Forgets every failed sign-in counted for the account, from all clients."""
    await service.unlock_sign_in(principal, id, request_id=current_request_id())
    return _no_content()


@router.get(
    "/users/{id}",
    operation_id="getUser",
    summary="A user.",
    response_description="The user.",
    responses=problems(401, 404, 422, 500),
)
async def get_user(id: uuid.UUID, service: Service, principal: SignedIn) -> api.User:
    """Users read their own account and admins read any. Anyone else's account is a 404, the same as an account that
    does not exist."""
    return _user(await service.user(principal, id))


@router.get(
    "/sessions/{id}",
    operation_id="getSession",
    summary="One of the signed-in user's sessions.",
    response_description="The session.",
    responses=problems(401, 404, 422, 500),
)
async def get_session(id: uuid.UUID, service: Service, current: Current) -> api.Session:
    """Another user's session is a 404, the same as a session that does not exist or has expired."""
    return _session(await service.session(current.principal, id), current.user, current.session_id)


@router.delete(
    "/sessions/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteSession",
    summary="Revoke one of the signed-in user's sessions.",
    response_description="The session was revoked.",
    responses=problems(401, 403, 404, 422, 500) | headers(204, CLEAR_SESSION_COOKIE),
)
async def delete_session(id: uuid.UUID, service: Service, current: Current, session: CurrentSession) -> Response:
    """Another user's session is a 404, as in getSession. Revoking the session the request carries also clears the
    cookie."""
    await service.revoke_session(current.principal, id)
    return _no_content(session.cookie.clear() if id == current.session_id else None)


@router.patch(
    "/users/{id}",
    operation_id="patchUser",
    summary="Change a user (admin only).",
    response_description="The user after the change.",
    responses=problems(400, 401, 403, 404, 409, 413, 415, 422, 500),
)
async def patch_user(
    id: uuid.UUID,
    body: Annotated[api.UserPatch, Body(media_type=MERGE_PATCH)],
    service: Service,
    principal: Admin,
) -> api.User:
    """JSON Merge Patch (RFC 7396): omitted fields are left unchanged. Disabling a user signs them out everywhere.
    Disabling the only enabled admin is refused with 409 `last_admin`."""
    # an empty patch returns the user unchanged
    if body.is_disabled is MISSING:
        return _user(await service.user(principal, id))
    user = await service.set_disabled(principal, id, disabled=body.is_disabled, request_id=current_request_id())
    return _user(user)


@router.delete(
    "/users/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteUser",
    summary="Delete a user (admin only).",
    response_description="Deleted.",
    responses=problems(401, 403, 404, 409, 422, 500),
)
async def delete_user(id: uuid.UUID, service: Service, current: CurrentAdmin) -> Response:
    """Deletes the account and everything it owns and ends all its sessions. Like deleting one's own account, the
    admin's session must have signed in within the last 10 minutes (403 `reauthentication_required`). The only
    enabled admin cannot be deleted (409 `last_admin`)."""
    # the service checks the recent sign-in
    await service.delete_user(current.principal, current.session_id, id, request_id=current_request_id())
    return _no_content()
