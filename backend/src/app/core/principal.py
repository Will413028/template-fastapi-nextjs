"""The signed-in user of the request being served (ADR fastapi-backend-stack D5: what modules need from auth goes
through app.core).

The auth module's middleware (app.modules.auth.dependencies) sets it from the session cookie before the contract is
checked, so the contract's security check (app.http.contract) and the routes' access markers (app.http.access) see it.
Services do not read it: a router passes the principal to a service explicitly.
"""

import contextvars
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from app.core.errors import AppError, Kind

if TYPE_CHECKING:
    import uuid


# The role that may administer users (users.role); app.http.access's ADMIN marker requires it.
ROLE_ADMIN: Final = "admin"


@dataclass(frozen=True, slots=True)
class Principal:
    id: uuid.UUID
    # read from the database on every request, so a role change takes effect on the next one
    role: str


# The code clients translate when a request has no valid session; changing it is a breaking change.
CODE_SESSION_INVALID = "session_invalid"


def session_invalid() -> AppError:
    """A missing, unknown, expired or revoked session, or a disabled user. The reason is not told apart: the client's
    remedy is always to sign in."""
    return AppError(Kind.UNAUTHENTICATED, CODE_SESSION_INVALID, "no valid session")


_principal: contextvars.ContextVar[Principal | None] = contextvars.ContextVar("principal", default=None)


def current_principal() -> Principal | None:
    """The signed-in user of the request being served; None for an anonymous request or outside one."""
    return _principal.get()


def set_principal(principal: Principal | None) -> contextvars.Token[Principal | None]:
    return _principal.set(principal)


def reset_principal(token: contextvars.Token[Principal | None]) -> None:
    _principal.reset(token)
