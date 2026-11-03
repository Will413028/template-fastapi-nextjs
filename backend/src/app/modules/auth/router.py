"""The auth operations of backend/api/openapi.yaml: accounts (/users) and sessions (/sessions) (Go version:
internal/httpapi/users.go). Request and response shapes are the contract's generated models (D8).

A 201 carries a Location whose GET answers 200 for the creator (docs/adr/2026-10-18-api-and-auth-baseline.md D9).
Sign-up and sign-in are limited per client address (RATE_LIMITED, D6 of the same ADR); sign-in is also throttled per
account and client in the service.
"""

import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Response, status

from app.modules.auth.dependencies import Current, CurrentSession, Service
from app.platform.errors import AppError, Kind
from app.platform.pagination import DEFAULT_LIMIT
from app.platform.principal import session_invalid
from app.web import contract_models as api
from app.web.access import PUBLIC, SignedIn
from app.web.origin import current_client
from app.web.ratelimit import RATE_LIMITED, limit_key
from app.web.request_id import current_request_id

if TYPE_CHECKING:
    from app.modules.auth.service import SessionInfo, User

# Routes match in the order they are declared: a literal path (/users/me, /sessions/current) comes before the
# template that would also match it (/users/{id}, /sessions/{id}), or that template's handler gets it.
router = APIRouter(prefix="/api")


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


def _client_key() -> str:
    """Where a sign-in comes from, for its throttle: the address the rate limit uses (IPv4, or the IPv6 /64). The
    connection always has one over TCP; without one every client would share the "unknown" bucket."""
    client = current_client()
    address = limit_key(client.address if client else None)
    return f"ip:{address if address is not None else 'unknown'}"


@router.post("/users", status_code=status.HTTP_201_CREATED, dependencies=[PUBLIC, RATE_LIMITED])
async def create_user(
    body: api.CreateUserRequest, service: Service, session: CurrentSession, response: Response
) -> api.User:
    user, new = await service.create_user(body.account, body.password)
    _issued(response, f"/api/users/{user.id}", session.cookie.set(new.token, new.max_age))
    return _user(user)


@router.post("/sessions", status_code=status.HTTP_201_CREATED, dependencies=[PUBLIC, RATE_LIMITED])
async def create_session(
    body: api.CreateSessionRequest, service: Service, session: CurrentSession, response: Response
) -> api.Session:
    user, new = await service.sign_in(
        body.account, body.password, previous_token=session.token, client_key=_client_key()
    )
    _issued(response, f"/api/sessions/{new.info.id}", session.cookie.set(new.token, new.max_age))
    return _session(new.info, user, new.info.id)


@router.delete("/sessions/current", status_code=status.HTTP_204_NO_CONTENT, dependencies=[PUBLIC])
async def delete_current_session(service: Service, session: CurrentSession) -> Response:
    authenticated = session.authenticated
    await service.sign_out(session.token, user_id=authenticated.user.id if authenticated else None)
    return _no_content(session.cookie.clear())


@router.get("/sessions")
async def list_sessions(
    service: Service, current: Current, limit: int = DEFAULT_LIMIT, cursor: str | None = None
) -> api.SessionPage:
    """The contract has already checked limit's range (app.web.contract)."""
    page = await service.sessions(current.principal, limit=limit, cursor=cursor)
    return api.SessionPage(
        sessions=[_session(s, current.user, current.session_id) for s in page.sessions], next_cursor=page.next_cursor
    )


@router.delete("/sessions", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_sessions(service: Service, current: Current, session: CurrentSession) -> Response:
    """Signs the user out everywhere, this browser included."""
    await service.sign_out_all(current.user.id)
    return _no_content(session.cookie.clear())


@router.delete("/users/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_current_user(service: Service, current: Current, session: CurrentSession) -> Response:
    await service.delete_own_account(current.principal, current.session_id, request_id=current_request_id())
    return _no_content(session.cookie.clear())


@router.get("/users/me")
async def get_current_user(current: Current) -> api.User:
    return _user(current.user)


@router.get("/sessions/current")
async def get_current_session(service: Service, current: Current) -> api.Session:
    """getSession for the session the request carries, as /users/me is for the user."""
    try:
        session = await service.session(current.principal, current.session_id)
    except AppError as err:
        if err.kind is not Kind.NOT_FOUND:
            raise
        # revoked or expired since the middleware read it: the request no longer has a session
        raise session_invalid() from err
    return _session(session, current.user, current.session_id)


@router.get("/users/{id}")
async def get_user(id: uuid.UUID, service: Service, principal: SignedIn) -> api.User:
    return _user(await service.user(principal, id))


@router.get("/sessions/{id}")
async def get_session(id: uuid.UUID, service: Service, current: Current) -> api.Session:
    return _session(await service.session(current.principal, id), current.user, current.session_id)


@router.delete("/sessions/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(id: uuid.UUID, service: Service, current: Current, session: CurrentSession) -> Response:
    """Revokes one of the user's sessions; revoking the one the request carries also clears its cookie."""
    await service.revoke_session(current.principal, id)
    return _no_content(session.cookie.clear() if id == current.session_id else None)
