"""Every auth query builds and compiles for PostgreSQL without a database.

The queries run only in the CI-only database tests; building them here catches, on this machine, what SQLAlchemy
refuses or deprecates while a statement is assembled (pytest turns the warning into an error). Found when the sign-in
throttle multiplied an Interval by an integer, which SQLAlchemy 2.1 deprecates.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from sqlalchemy.dialects import postgresql

from app.modules.auth import repository

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncConnection
    from sqlalchemy.sql import ClauseElement

NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)
ID = uuid.UUID("01900000-0000-7000-8000-000000000001")
STORED_HASH = "$argon2id$v=19$m=64,t=1,p=1$c2FsdA$a2V5"


class _NoRows:
    def one_or_none(self) -> None:
        return None

    def scalar_one_or_none(self) -> None:
        return None

    def one(self) -> tuple[uuid.UUID, datetime, datetime, datetime]:
        return (ID, NOW, NOW, NOW)


class CompilingConnection:
    """Stands in for AsyncConnection: compiles each statement for PostgreSQL and answers with no rows."""

    def __init__(self) -> None:
        self.sql: list[str] = []

    async def execute(self, statement: ClauseElement) -> _NoRows:
        self.sql.append(str(statement.compile(dialect=postgresql.dialect())))
        return _NoRows()


def run(query: Callable[[AsyncConnection], Awaitable[object]]) -> str:
    conn = CompilingConnection()
    asyncio.run(query(cast("AsyncConnection", conn)))
    [sql] = conn.sql
    return sql


def test_every_query_builds() -> None:
    queries: dict[str, Callable[[AsyncConnection], Awaitable[object]]] = {
        "insert_user_if_absent": lambda c: repository.insert_user_if_absent(
            c, account="alice", password_hash=STORED_HASH, role="user"
        ),
        "get_user": lambda c: repository.get_user(c, ID),
        "get_login_user": lambda c: repository.get_login_user(c, "alice"),
        "insert_session": lambda c: repository.insert_session(
            c, token_hash=b"x" * 32, user_id=ID, now=NOW, expires_at=NOW
        ),
        "get_session_with_user": lambda c: repository.get_session_with_user(c, b"x" * 32),
        "touch_session": lambda c: repository.touch_session(c, b"x" * 32, NOW),
        "delete_session": lambda c: repository.delete_session(c, b"x" * 32),
        "get_user_session": lambda c: repository.get_user_session(
            c, session_id=ID, user_id=ID, now=NOW, idle=timedelta(days=1)
        ),
        "reserve_login_attempt": lambda c: repository.reserve_login_attempt(
            c, account_key="alice", client_key="ip:192.0.2.1", now=NOW
        ),
        "get_login_throttle": lambda c: repository.get_login_throttle(c, account_key="alice", client_key="ip:1"),
        "clear_login_throttles": lambda c: repository.clear_login_throttles(c, "alice"),
    }
    public = {name for name in vars(repository) if not name.startswith("_") and callable(getattr(repository, name))}
    functions = {name for name in public if getattr(getattr(repository, name), "__module__", "") == repository.__name__}
    assert functions == queries.keys()  # a new query is added here too
    for name, query in queries.items():
        assert run(query), name


def test_the_throttle_waits_in_whole_minutes() -> None:
    sql = run(lambda c: repository.reserve_login_attempt(c, account_key="a", client_key="ip:1", now=NOW))
    assert "ON CONFLICT (account_key, client_key) DO UPDATE" in sql
    assert "make_interval(" in sql
    assert (
        "WHERE sign_in_throttles.next_allowed_at IS NULL OR sign_in_throttles.next_allowed_at <= excluded.updated_at"
        in sql
    )
