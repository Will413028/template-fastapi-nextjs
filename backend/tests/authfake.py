"""The real API app around a stand-in AuthService, for the HTTP behaviour of authentication that needs no database:
the middleware, the contract's security check, the cookie and the access markers. What the service does with the
database is tested against PostgreSQL (test_auth_db.py)."""

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, override

from sqlalchemy.ext.asyncio import create_async_engine

from apitest import contract, deps
from app.main.factory import create_app
from app.modules.auth.dependencies import Auth, SessionCookie
from app.modules.auth.service import Authenticated, AuthService, CleanupCounts, SessionInfo, User
from app.platform.errors import AppError, Kind
from app.platform.principal import Principal, session_invalid

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.web.origin import OriginPolicy

SECURE = SessionCookie(name="__Host-session", secure=True)
ALICE = User(
    id=uuid.UUID("01900000-0000-7000-8000-000000000001"),
    account="alice",
    role="user",
    is_disabled=False,
    created_at=datetime(2026, 10, 18, 12, tzinfo=UTC),
)
SESSION_ID = uuid.UUID("01900000-0000-7000-8000-0000000000aa")
TOKEN = "t" * 43


class FakeAuthService(AuthService):
    """Knows one session, TOKEN for ALICE, and counts the lookups."""

    def __init__(self) -> None:
        # never connects: every method that would reach the database is replaced
        engine = create_async_engine("postgresql+asyncpg://nobody@127.0.0.1:1/none")
        super().__init__(engine, session_absolute=timedelta(days=7), session_idle=timedelta(days=1))
        self.lookups = 0
        self.signed_out: list[str | None] = []
        self.cleanups = 0
        # cleanup raises on the runs whose number (from 1) is in here
        self.cleanup_fails: set[int] = set()

    @override
    async def authenticate(self, token: str) -> Authenticated:
        self.lookups += 1
        if token != TOKEN:
            raise session_invalid()
        return Authenticated(user=ALICE, session_id=SESSION_ID)

    @override
    async def sign_out(self, token: str | None, *, user_id: uuid.UUID | None) -> None:
        self.signed_out.append(token)

    @override
    async def session(self, caller: Principal, session_id: uuid.UUID) -> SessionInfo:
        if (caller.id, session_id) != (ALICE.id, SESSION_ID):
            raise AppError(Kind.NOT_FOUND, "", "session not found")
        at = ALICE.created_at
        return SessionInfo(id=SESSION_ID, created_at=at, last_used_at=at, expires_at=at + timedelta(days=7))

    @override
    async def cleanup(self) -> CleanupCounts:
        self.cleanups += 1
        if self.cleanups in self.cleanup_fails:
            msg = "the database is down"
            raise OSError(msg)
        return CleanupCounts(sessions=1, throttles=2)


def fake_app(
    service: FakeAuthService,
    *,
    cookie: SessionCookie = SECURE,
    origin: OriginPolicy | None = None,
    cleanup_every: float = 3600.0,
) -> FastAPI:
    base = deps(contract())
    return create_app(
        dataclasses.replace(
            base, auth=Auth(service, cookie), origin=origin or base.origin, cleanup_every=cleanup_every
        ),
    )
