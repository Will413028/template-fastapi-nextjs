"""The PostgreSQL connection pool and the declarative base every module's tables share.

The engine connects lazily, so a database that is briefly unavailable does not fail startup; readiness
(`Database.ping`) reports it instead. Every connection sets statement_timeout, lock_timeout,
idle_in_transaction_session_timeout and TimeZone=UTC, so a runaway query, a lock queue or an abandoned transaction
cannot hold a connection.
"""

import ssl
from typing import TYPE_CHECKING, Self

from sqlalchemy import MetaData, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import DeclarativeBase

if TYPE_CHECKING:
    from app.platform.config import DatabaseSettings

# Ends transactions a caller forgot to finish; longer than the statement timeout because a transaction legitimately
# spans several statements.
IDLE_IN_TRANSACTION_TIMEOUT_SECONDS = 30
# A connection older than this is replaced, as pgxpool's MaxConnLifetime does for the Go version.
POOL_RECYCLE_SECONDS = 3600

# Column building blocks every module's models share (docs/rules/database-design.md, 型別選擇).
UUIDV7 = text("uuidv7()")
NOW = text("now()")
TIMESTAMPTZ = TIMESTAMP(timezone=True)


class Base(DeclarativeBase):
    """Each module's models.py declares its tables on this base, so one metadata holds the whole schema."""

    metadata = MetaData()


def engine_url(settings: DatabaseSettings) -> URL:
    """The SQLAlchemy URL. It carries the password, which SQLAlchemy masks whenever the URL is printed."""
    parts = settings.parts
    return URL.create(
        "postgresql+asyncpg",
        username=settings.user,
        password=settings.password,
        host=parts.hostname,
        port=parts.port,
        database=settings.database_name,
    )


def connect_args(settings: DatabaseSettings) -> dict[str, object]:
    """asyncpg's connection arguments: TLS and the session settings every connection starts with."""
    server_settings = {
        "lock_timeout": _millis(settings.lock_timeout),
        "idle_in_transaction_session_timeout": _millis(IDLE_IN_TRANSACTION_TIMEOUT_SECONDS),
        "TimeZone": "UTC",
    }
    if settings.statement_timeout is not None:
        server_settings["statement_timeout"] = _millis(settings.statement_timeout)
    # verify-full checks the certificate chain against the system's trust store and the host name; asyncpg's own
    # verify-full would look for ~/.postgresql/root.crt instead.
    tls: ssl.SSLContext | str = ssl.create_default_context() if settings.sslmode == "verify-full" else settings.sslmode
    return {"ssl": tls, "server_settings": server_settings}


def create_engine(settings: DatabaseSettings) -> AsyncEngine:
    return create_async_engine(
        engine_url(settings),
        pool_size=settings.max_conns,
        max_overflow=0,
        # a connection the database or the network dropped (a postgres restart) is replaced before a request gets it,
        # as pgxpool pings connections idle for a second or more
        pool_pre_ping=True,
        pool_recycle=POOL_RECYCLE_SECONDS,
        connect_args=connect_args(settings),
    )


class Database:
    """The application's connection pool."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    @classmethod
    def open(cls, settings: DatabaseSettings) -> Self:
        return cls(create_engine(settings))

    async def ping(self) -> None:
        """Raises when the database cannot answer a query."""
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def close(self) -> None:
        await self.engine.dispose()


def _millis(seconds: float) -> str:
    return str(round(seconds * 1000))
