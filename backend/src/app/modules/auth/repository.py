"""The auth module's queries (Go version: internal/modules/auth/queries/*.sql).

Each function runs one statement on the connection it is given; the service decides the transaction. Statements are
SQLAlchemy Core expressions over the tables in models.py, never SQL assembled from strings.
"""

from typing import TYPE_CHECKING

from sqlalchemy import Integer, Interval, Row, case, delete, func, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.modules.auth.models import Session, SignInThrottle, User

if TYPE_CHECKING:
    import uuid
    from datetime import datetime, timedelta

    from sqlalchemy.ext.asyncio import AsyncConnection

# what the API shows of a user, in this order (service.User)
USER_COLUMNS = (User.id, User.account, User.role, User.is_disabled, User.created_at)
# what the API shows of a session (service.SessionInfo)
SESSION_COLUMNS = (Session.id, Session.created_at, Session.last_used_at, Session.expires_at)

# From the 5th consecutive failure on, the next attempt waits 2^(failures - 5) minutes, at most an hour.
THROTTLE_FREE_ATTEMPTS = 5
_MAX_WAIT_MINUTES = 60
_ONE_MINUTE = literal_column("interval '1 minute'", Interval())


async def insert_user_if_absent(
    conn: AsyncConnection, *, account: str, password_hash: str, role: str
) -> Row[uuid.UUID, str, str, bool, datetime] | None:
    """The new user, or None when the account is taken: the unique index decides, so concurrent sign-ups for one
    account get exactly one row."""
    stmt = (
        insert(User)
        .values(account=account, password_hash=password_hash, role=role)
        .on_conflict_do_nothing(index_elements=[User.account])
        .returning(*USER_COLUMNS)
    )
    return (await conn.execute(stmt)).one_or_none()


async def get_user(conn: AsyncConnection, user_id: uuid.UUID) -> Row[uuid.UUID, str, str, bool, datetime] | None:
    return (await conn.execute(select(*USER_COLUMNS).where(User.id == user_id))).one_or_none()


async def get_login_user(conn: AsyncConnection, account: str) -> Row[uuid.UUID, str, str, bool, datetime, str] | None:
    """The user and its password hash, for checking a sign-in."""
    stmt = select(*USER_COLUMNS, User.password_hash).where(User.account == account)
    return (await conn.execute(stmt)).one_or_none()


async def insert_session(
    conn: AsyncConnection, *, token_hash: bytes, user_id: uuid.UUID, now: datetime, expires_at: datetime
) -> Row[uuid.UUID, datetime, datetime, datetime]:
    stmt = (
        insert(Session)
        .values(token_hash=token_hash, user_id=user_id, created_at=now, last_used_at=now, expires_at=expires_at)
        .returning(*SESSION_COLUMNS)
    )
    return (await conn.execute(stmt)).one()


async def get_session_with_user(
    conn: AsyncConnection, token_hash: bytes
) -> Row[uuid.UUID, datetime, datetime, uuid.UUID, str, str, bool, datetime] | None:
    """The session with its user; expiry and is_disabled are the caller's to check."""
    stmt = (
        select(Session.id, Session.last_used_at, Session.expires_at, *USER_COLUMNS)
        .join(User, User.id == Session.user_id)
        .where(Session.token_hash == token_hash)
    )
    return (await conn.execute(stmt)).one_or_none()


async def touch_session(conn: AsyncConnection, token_hash: bytes, now: datetime) -> None:
    await conn.execute(update(Session).where(Session.token_hash == token_hash).values(last_used_at=now))


async def delete_session(conn: AsyncConnection, token_hash: bytes) -> None:
    await conn.execute(delete(Session).where(Session.token_hash == token_hash))


async def get_user_session(
    conn: AsyncConnection, *, session_id: uuid.UUID, user_id: uuid.UUID, now: datetime, idle: timedelta
) -> Row[uuid.UUID, datetime, datetime, datetime] | None:
    """The user's live session with session_id: another user's, an expired one and one Authenticate would refuse for
    idleness are all not found, before the cleanup removes the row (docs/adr/2026-10-18-api-and-auth-baseline.md D9)."""
    stmt = select(*SESSION_COLUMNS).where(
        Session.id == session_id,
        Session.user_id == user_id,
        Session.expires_at > now,
        Session.last_used_at > now - idle,
    )
    return (await conn.execute(stmt)).one_or_none()


async def reserve_login_attempt(conn: AsyncConnection, *, account_key: str, client_key: str, now: datetime) -> bool:
    """Counts an attempt from one client as a failure before the password is checked, in one statement: the row lock
    serializes concurrent attempts, so they cannot all pass a wait that has not been recorded yet. False, and nothing
    counted, while the client must still wait (docs/adr/2026-10-18-api-and-auth-baseline.md D6; OWASP Authentication
    Cheat Sheet)."""
    stmt = insert(SignInThrottle).values(
        account_key=account_key, client_key=client_key, failures=1, next_allowed_at=None, updated_at=now
    )
    failures = SignInThrottle.failures + 1
    # the exponent is capped first, so power() cannot overflow
    wait_minutes = func.least(
        func.power(2, func.least(failures - THROTTLE_FREE_ATTEMPTS, 6)).cast(Integer), _MAX_WAIT_MINUTES
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[SignInThrottle.account_key, SignInThrottle.client_key],
        set_={
            "failures": failures,
            "next_allowed_at": case(
                (failures >= THROTTLE_FREE_ATTEMPTS, stmt.excluded.updated_at + _ONE_MINUTE * wait_minutes),
                else_=None,
            ),
            "updated_at": stmt.excluded.updated_at,
        },
        where=or_(SignInThrottle.next_allowed_at.is_(None), SignInThrottle.next_allowed_at <= stmt.excluded.updated_at),
    ).returning(SignInThrottle.failures)
    return (await conn.execute(stmt)).one_or_none() is not None


async def get_login_throttle(conn: AsyncConnection, *, account_key: str, client_key: str) -> datetime | None:
    """When the client may try again; None when it need not wait or has no row."""
    stmt = select(SignInThrottle.next_allowed_at).where(
        SignInThrottle.account_key == account_key, SignInThrottle.client_key == client_key
    )
    return (await conn.execute(stmt)).scalar_one_or_none()


async def clear_login_throttles(conn: AsyncConnection, account_key: str) -> None:
    """Forgets every count for the account: after a successful sign-in."""
    await conn.execute(delete(SignInThrottle).where(SignInThrottle.account_key == account_key))
