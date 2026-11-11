"""Who may call an operation (docs/adr/2026-10-18-api-and-auth-baseline.md D7, ADR fastapi-backend-stack D5).

Every /api route carries exactly one access marker, and app.main checks at startup that it does (check_routes): a new
operation's access is a decision, not a default. The contract's security follows the markers (SIGNED_IN and ADMIN
declare the session cookie, PUBLIC nothing). app.http.route.ApiRoute runs a route's marker before FastAPI reads the
body, so an anonymous or unauthorized caller is refused before anything about its request is checked; SIGNED_IN and
ADMIN then give the route the principal.

    @router.get("/items/{id}")
    async def get_item(id: uuid.UUID, principal: SignedIn) -> Item: ...

    @router.post("/sessions", dependencies=[PUBLIC, RATE_LIMITED])
    async def create_session(...) -> Session: ...

    @router.get("/users")
    async def list_users(principal: Admin) -> UserPage: ...

ADMIN does not build on SIGNED_IN: a marker inside another would give the route two (app.main.check_routes counts the
markers among all of a route's dependencies).
"""

import logging
from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyCookie

from app.core.config import SECURE_SESSION_COOKIE
from app.core.errors import AppError, Kind
from app.core.principal import ROLE_ADMIN, Principal, current_principal, session_invalid

logger = logging.getLogger(__name__)

# The code clients translate when the signed-in user's role may not call the operation; changing it is a breaking
# change.
CODE_INSUFFICIENT_ROLE = "insufficient_role"


# The contract's security scheme, so SIGNED_IN and ADMIN operations declare it. Documentation only: it never refuses a
# request (auto_error=False) and its value is not read; AuthenticateMiddleware resolves the cookie, whichever name
# COOKIE_SECURE gives it.
_SESSION_COOKIE = APIKeyCookie(
    name=SECURE_SESSION_COOKIE,
    scheme_name="sessionCookie",
    description="Opaque session token, HttpOnly. Named `session` instead when the server runs with COOKIE_SECURE=false "
    "(local http development), because the `__Host-` prefix requires Secure.",
    auto_error=False,
)
_Documented = Annotated[str | None, Security(_SESSION_COOKIE)]


async def public() -> None:
    """The marker of an operation anyone may call."""


async def signed_in(_: _Documented = None) -> Principal:
    """The marker of an operation that needs a session; returns the signed-in user. Without a valid session (the
    middleware found none, or the cookie is stale or forged) a 401 with code session_invalid."""
    principal = current_principal()
    if principal is None:
        raise session_invalid()
    return principal


async def admin(request: Request, _: _Documented = None) -> Principal:
    """The marker of an operation only admins may call; returns the signed-in admin. The role is the one the
    middleware read from the database for this request, so a demotion takes effect on the next one. A refusal is
    logged as authz.denied (OWASP Logging Cheat Sheet: authorization failures)."""
    principal = await signed_in()
    if principal.role != ROLE_ADMIN:
        route = request.scope.get("route")
        operation = f"{request.method} {getattr(route, 'path_format', request.url.path)}"
        logger.warning(
            "operation refused", extra={"event": "authz.denied", "user_id": str(principal.id), "operation": operation}
        )
        raise AppError(Kind.FORBIDDEN, CODE_INSUFFICIENT_ROLE, "the signed-in user's role may not do this")
    return principal


PUBLIC = Depends(public)
SIGNED_IN = Depends(signed_in)
ADMIN = Depends(admin)
SignedIn = Annotated[Principal, SIGNED_IN]
Admin = Annotated[Principal, ADMIN]

# The markers app.main looks for in each route's dependencies.
MARKERS = frozenset({public, signed_in, admin})
