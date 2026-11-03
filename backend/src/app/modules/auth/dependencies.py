"""The auth module's HTTP edge: the session cookie, the middleware that authenticates every /api request, and the
dependencies its routes use (Go version: internal/httpapi/auth.go).

The session token lives only in an HttpOnly cookie (docs/adr/2026-10-18-api-and-auth-baseline.md D4). The middleware
runs before the contract is checked, so an operation that declares security sees an anonymous request as 401 rather
than a 422 about its body (app.web.contract). A missing or invalid session leaves the request anonymous: operations
without security (sign-in, sign-up, sign-out) must work with a stale cookie. An invalid cookie is cleared on the
response: it is HttpOnly, so only the server can remove it, and the frontend takes a present cookie for a session.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request
from starlette.requests import cookie_parser

from app.modules.auth.service import Authenticated, AuthService
from app.platform.errors import AppError, Kind
from app.platform.principal import Principal, reset_principal, session_invalid, set_principal
from app.web.access import ADMIN, SIGNED_IN

if TYPE_CHECKING:
    from datetime import timedelta

    from starlette.types import ASGIApp, Message, Receive, Scope, Send

# where the middleware leaves the request's session for the routes (an ASGI scope extension key)
_SCOPE_KEY = "app.auth"


@dataclass(frozen=True, slots=True)
class SessionCookie:
    """Every cookie the API sets: HttpOnly so scripts cannot read it, Path=/ and no Domain (the __Host- prefix needs
    both), SameSite=Lax, Secure unless COOKIE_SECURE=false for local development over http. name follows secure
    (app.platform.config.Settings.session_cookie_name)."""

    name: str
    secure: bool

    def set(self, token: str, max_age: timedelta) -> str:
        """The Set-Cookie value for a new session, with Max-Age its absolute lifetime."""
        return self._cookie(token, int(max_age.total_seconds()))

    def clear(self) -> str:
        """The Set-Cookie value that removes the session cookie."""
        return self._cookie("", 0)

    def _cookie(self, value: str, max_age: int) -> str:
        # value is a token this server made (base64url) or empty: nothing to quote
        attributes = [f"{self.name}={value}", "Path=/", f"Max-Age={max_age}", "HttpOnly"]
        if self.secure:
            attributes.append("Secure")
        attributes.append("SameSite=Lax")
        return "; ".join(attributes)


@dataclass(frozen=True, slots=True)
class Auth:
    """What app.main needs to mount the auth module."""

    service: AuthService
    cookie: SessionCookie


@dataclass(frozen=True, slots=True)
class RequestSession:
    """The session cookie the request carried (valid or not, for sign-in and sign-out), what it authenticated, the
    cookie's settings, and the service the routes call."""

    token: str | None
    authenticated: Authenticated | None
    cookie: SessionCookie
    service: AuthService


class AuthenticateMiddleware:
    """Resolves the session cookie of every /api request into the principal (app.platform.principal) and the
    RequestSession. A database error is not caught: RecoverMiddleware turns it into a 500."""

    def __init__(self, app: ASGIApp, auth: Auth) -> None:
        self.app = app
        self.service = auth.service
        self.cookie = auth.cookie

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path: str = scope.get("path", "")
        if scope["type"] != "http" or not (path == "/api" or path.startswith("/api/")):
            await self.app(scope, receive, send)
            return
        token = self._token(scope)
        authenticated: Authenticated | None = None
        if token:
            try:
                authenticated = await self.service.authenticate(token)
            except AppError as err:
                if err.kind is not Kind.UNAUTHENTICATED:
                    raise
                send = self._clearing_cookie(send)
        scope[_SCOPE_KEY] = RequestSession(
            token=token, authenticated=authenticated, cookie=self.cookie, service=self.service
        )
        reset_token = set_principal(authenticated.principal if authenticated else None)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_principal(reset_token)

    def _token(self, scope: Scope) -> str | None:
        cookies: dict[str, str] = {}
        for name, value in scope["headers"]:
            if name == b"cookie":
                cookies |= cookie_parser(value.decode("latin-1"))
        return cookies.get(self.cookie.name) or None

    def _clearing_cookie(self, send: Send) -> Send:
        """send, adding the Set-Cookie that clears the dead session cookie unless the response sets that cookie
        itself (a sign-in that issues a new one)."""
        prefix = f"{self.cookie.name}=".encode("latin-1")
        clear = self.cookie.clear().encode("latin-1")

        async def clearing(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers: list[tuple[bytes, bytes]] = list(message.get("headers", []))
                if not any(name == b"set-cookie" and value.startswith(prefix) for name, value in headers):
                    headers.append((b"set-cookie", clear))
                message = {**message, "headers": headers}
            await send(message)

        return clearing


def request_session(request: Request) -> RequestSession:
    found = request.scope.get(_SCOPE_KEY)
    if not isinstance(found, RequestSession):
        msg = "the auth routes run inside AuthenticateMiddleware (app.main.factory)"
        raise TypeError(msg)
    return found


CurrentSession = Annotated[RequestSession, Depends(request_session)]
# the access markers, from inside Current and CurrentAdmin: a route that takes the current user needs a session, and
# one that takes the current admin needs the admin role
_SignedIn = Annotated[Principal, SIGNED_IN]
_Admin = Annotated[Principal, ADMIN]


def auth_service(session: CurrentSession) -> AuthService:
    return session.service


def _authenticated(principal: Principal, session: RequestSession) -> Authenticated:
    authenticated = session.authenticated
    if authenticated is None or authenticated.user.id != principal.id:
        raise session_invalid()
    return authenticated


async def current(principal: _SignedIn, session: CurrentSession) -> Authenticated:
    """The signed-in user with its session; it carries the SIGNED_IN marker, so a route that takes it needs no
    other."""
    return _authenticated(principal, session)


async def current_admin(principal: _Admin, session: CurrentSession) -> Authenticated:
    """The signed-in admin with its session; it carries the ADMIN marker, so a route that takes it needs no other."""
    return _authenticated(principal, session)


Service = Annotated[AuthService, Depends(auth_service)]
Current = Annotated[Authenticated, Depends(current)]
CurrentAdmin = Annotated[Authenticated, Depends(current_admin)]
