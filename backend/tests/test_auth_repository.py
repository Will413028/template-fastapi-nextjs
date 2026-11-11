"""Every auth query builds and compiles for PostgreSQL without a database.

The queries run only in the CI-only database tests; building them here catches, on this machine, what SQLAlchemy
refuses or deprecates while a statement is assembled (pytest turns the warning into an error). Found when the sign-in
throttle multiplied an Interval by an integer, which SQLAlchemy 2.1 deprecates.
"""

import asyncio
import inspect
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
LIVE = repository.Live(user_id=ID, now=NOW, idle=timedelta(days=1))
STORED_HASH = "$argon2id$v=19$m=64,t=1,p=1$c2FsdA$a2V5"


class _NoRows:
    rowcount = 0

    def all(self) -> list[object]:
        return []

    def scalars(self) -> list[object]:
        return []

    def one_or_none(self) -> None:
        return None

    def scalar_one_or_none(self) -> None:
        return None

    def scalar_one(self) -> int:
        return 1

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
        "get_user_session": lambda c: repository.get_user_session(c, ID, LIVE),
        "reserve_login_attempt": lambda c: repository.reserve_login_attempt(
            c, account_key="alice", client_key="ip:192.0.2.1", now=NOW
        ),
        "get_login_throttle": lambda c: repository.get_login_throttle(c, account_key="alice", client_key="ip:1"),
        "clear_login_throttle": lambda c: repository.clear_login_throttle(c, account_key="alice", client_key="ip:1"),
        "list_user_sessions": lambda c: repository.list_user_sessions(c, LIVE, after=ID, rows=21),
        "delete_user_session": lambda c: repository.delete_user_session(c, ID, LIVE),
        "delete_user_sessions": lambda c: repository.delete_user_sessions(c, ID),
        "get_session_created_at": lambda c: repository.get_session_created_at(c, session_id=ID, user_id=ID),
        "delete_expired_sessions": lambda c: repository.delete_expired_sessions(c, now=NOW, idle=timedelta(days=1)),
        "lock_enabled_admins": repository.lock_enabled_admins,
        "list_users": lambda c: repository.list_users(c, after=ID, rows=2),
        "set_user_disabled": lambda c: repository.set_user_disabled(c, ID, disabled=True),
        "recover_admin": lambda c: repository.recover_admin(c, account="root", password_hash=STORED_HASH),
        "delete_user": lambda c: repository.delete_user(c, ID),
        "insert_audit_event": lambda c: repository.insert_audit_event(
            c, repository.AuditRecord(at=NOW, actor_id=None, action="user.deleted", target_id=ID, request_id=None)
        ),
        "delete_stale_login_throttles": lambda c: repository.delete_stale_login_throttles(c, NOW),
        "clear_login_throttles": lambda c: repository.clear_login_throttles(c, "alice"),
        "count_untrusted_attempt": lambda c: repository.count_untrusted_attempt(c, account_key="alice", now=NOW),
        "get_untrusted_failures": lambda c: repository.get_untrusted_failures(c, "alice"),
        "insert_trusted_device": lambda c: repository.insert_trusted_device(
            c, token_hash=b"x" * 32, user_id=ID, now=NOW, expires_at=NOW
        ),
        "get_trusted_device": lambda c: repository.get_trusted_device(c, b"x" * 32, NOW),
        "delete_user_trusted_devices": lambda c: repository.delete_user_trusted_devices(c, ID),
        "delete_expired_trusted_devices": lambda c: repository.delete_expired_trusted_devices(c, NOW),
    }
    functions = {
        name
        for name, value in vars(repository).items()
        if not name.startswith("_") and inspect.isfunction(value) and value.__module__ == repository.__name__
    }
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


def test_signing_in_clears_only_that_clients_count() -> None:
    # ADR account-abuse-and-lifecycle D4: the owner signing in elsewhere does not lift the wait an attacker built up at
    # another address; the account-wide count ('') goes with it (NIST SP 800-63B-4 3.2.2)
    sql = run(lambda c: repository.clear_login_throttle(c, account_key="a", client_key="ip:1"))
    where = sql.partition("WHERE")[2]
    assert "sign_in_throttles.account_key = " in where
    assert "sign_in_throttles.client_key IN (" in where


def test_unlocking_clears_every_count_of_the_account() -> None:
    where = run(lambda c: repository.clear_login_throttles(c, "a")).partition("WHERE")[2]
    assert "sign_in_throttles.account_key = " in where
    assert "client_key" not in where


def test_recovery_changes_only_an_admin() -> None:
    # recover-admin never promotes an account (Go version: users.sql RecoverAdmin)
    changes, _, where = run(lambda c: repository.recover_admin(c, account="root", password_hash=STORED_HASH)).partition(
        "WHERE"
    )
    assert "users.account = " in where
    assert "users.role = " in where
    assert "is_disabled=" in changes
    assert "updated_at=now()" in changes


def test_deleting_an_account_locks_the_enabled_admins() -> None:
    sql = run(repository.lock_enabled_admins)
    assert sql.endswith("FOR UPDATE")
    assert "users.role = " in sql
    assert "users.is_disabled IS false" in sql
