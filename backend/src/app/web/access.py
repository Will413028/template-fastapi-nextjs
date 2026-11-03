"""Who may call an operation (docs/adr/2026-10-18-api-and-auth-baseline.md D7, plan D7).

Every /api route carries exactly one access marker, and app.main checks at startup that it does and that PUBLIC is
exactly the operations the contract gives `security: []` (app.main.routes): a new operation's access is a decision,
not a default. The contract's security already refuses an anonymous call before validation (app.web.contract); the
marker is the route's own statement of it, and SIGNED_IN gives the route the principal.

    @router.get("/items/{id}")
    async def get_item(id: uuid.UUID, principal: SignedIn) -> Item: ...

    @router.post("/sessions", dependencies=[PUBLIC, RATE_LIMITED])
    async def create_session(...) -> Session: ...
"""

from typing import Annotated

from fastapi import Depends

from app.platform.principal import Principal, current_principal, session_invalid


async def public() -> None:
    """The marker of an operation anyone may call."""


async def signed_in() -> Principal:
    """The marker of an operation that needs a session; returns the signed-in user. The contract check refuses an
    anonymous request before it gets here, so a missing principal means the route and the contract disagree."""
    principal = current_principal()
    if principal is None:
        raise session_invalid()
    return principal


PUBLIC = Depends(public)
SIGNED_IN = Depends(signed_in)
SignedIn = Annotated[Principal, SIGNED_IN]

# The markers app.main.routes looks for in each route's dependencies.
MARKERS = frozenset({public, signed_in})
