"""The auth module's queries (Go version: internal/modules/auth/queries/*.sql).

Each function runs one statement on the connection it is given; the service decides the transaction. Statements are
SQLAlchemy Core expressions over the tables in models.py, never SQL assembled from strings.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import Integer, Row, case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.modules.auth.models import AuditEvent, Session, SignInThrottle, TrustedDevice, User

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence
    from datetime import datetime, timedelta

    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncConnection

# what the API shows of a user, in this order (service.User)
USER_COLUMNS = (User.id, User.account, User.role, User.is_disabled, User.created_at)
# what the API shows of a session (service.SessionInfo)
SESSION_COLUMNS = (Session.id, Session.created_at, Session.last_used_at, Session.expires_at)

# the sign_in_throttles client_key of the account-wide count of clients without a trusted device
UNTRUSTED = ""

# From the 5th consecutive failure on, the next attempt waits 2^(failures - 5) minutes, at most an hour.
THROTTLE_FREE_ATTEMPTS = 5
_MAX_WAIT_MINUTES = 60


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


async def list_users(
    conn: AsyncConnection, *, after: uuid.UUID | None, rows: int
) -> Sequence[Row[uuid.UUID, str, str, bool, datetime]]:
    """Every user, disabled ones included, newest first (UUIDv7 ids sort by creation time), after the cursor id."""
    stmt = select(*USER_COLUMNS)
    if after is not None:
        stmt = stmt.where(User.id < after)
    return (await conn.execute(stmt.order_by(User.id.desc()).limit(rows))).all()


async def set_user_disabled(
    conn: AsyncConnection, user_id: uuid.UUID, *, disabled: bool
) -> Row[uuid.UUID, str, str, bool, datetime] | None:
    """Sets is_disabled and returns the user; None when there is no such user."""
    stmt = update(User).where(User.id == user_id).values(is_disabled=disabled).returning(*USER_COLUMNS)
    return (await conn.execute(stmt)).one_or_none()


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


@dataclass(frozen=True, slots=True)
class Live:
    """The user's sessions that authenticate would still accept at now: before expires_at and used within idle. The
    session resource sees only these, so another user's session, an expired one, and one authenticate would refuse
    for idleness are all not found before the cleanup removes the row (docs/adr/2026-10-18-api-and-auth-baseline.md
    D9)."""

    user_id: uuid.UUID
    now: datetime
    idle: timedelta

    def where(self) -> tuple[ColumnElement[bool], ...]:
        return (
            Session.user_id == self.user_id,
            Session.expires_at > self.now,
            Session.last_used_at > self.now - self.idle,
        )


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One administrative change: actor_id None is the operator on the host; request_id None outside a request."""

    at: datetime
    actor_id: uuid.UUID | None
    action: str
    target_id: uuid.UUID
    request_id: str | None


async def get_user_session(
    conn: AsyncConnection, session_id: uuid.UUID, live: Live
) -> Row[uuid.UUID, datetime, datetime, datetime] | None:
    return (await conn.execute(select(*SESSION_COLUMNS).where(Session.id == session_id, *live.where()))).one_or_none()


async def list_user_sessions(
    conn: AsyncConnection, live: Live, *, after: uuid.UUID | None, rows: int
) -> Sequence[Row[uuid.UUID, datetime, datetime, datetime]]:
    """The live sessions newest first (UUIDv7 ids sort by creation time), after the cursor id when given."""
    stmt = select(*SESSION_COLUMNS).where(*live.where())
    if after is not None:
        stmt = stmt.where(Session.id < after)
    return (await conn.execute(stmt.order_by(Session.id.desc()).limit(rows))).all()


async def delete_user_session(conn: AsyncConnection, session_id: uuid.UUID, live: Live) -> bool:
    """Deletes the live session with session_id; False when there is none."""
    return (await conn.execute(delete(Session).where(Session.id == session_id, *live.where()))).rowcount > 0


async def delete_user_sessions(conn: AsyncConnection, user_id: uuid.UUID) -> None:
    await conn.execute(delete(Session).where(Session.user_id == user_id))


async def get_session_created_at(
    conn: AsyncConnection, *, session_id: uuid.UUID, user_id: uuid.UUID
) -> datetime | None:
    """When the user's session was issued, that is when they last signed in with it."""
    stmt = select(Session.created_at).where(Session.id == session_id, Session.user_id == user_id)
    return (await conn.execute(stmt)).scalar_one_or_none()


async def delete_expired_sessions(conn: AsyncConnection, *, now: datetime, idle: timedelta) -> int:
    stmt = delete(Session).where(or_(Session.expires_at <= now, Session.last_used_at <= now - idle))
    return (await conn.execute(stmt)).rowcount


async def lock_enabled_admins(conn: AsyncConnection) -> list[uuid.UUID]:
    """The enabled admins' ids, locked until the transaction ends: two admins deleting themselves at once cannot both
    see the other still there."""
    stmt = select(User.id).where(User.role == "admin", User.is_disabled.is_(False)).order_by(User.id).with_for_update()
    return list((await conn.execute(stmt)).scalars())


async def delete_user(conn: AsyncConnection, user_id: uuid.UUID) -> bool:
    """Deletes the account; its sessions and every module's rows that reference it go with it (ON DELETE CASCADE).
    False when there was no such user."""
    return (await conn.execute(delete(User).where(User.id == user_id))).rowcount > 0


async def insert_audit_event(conn: AsyncConnection, record: AuditRecord) -> None:
    """Records one administrative change; callers run it in the change's own transaction."""
    await conn.execute(
        insert(AuditEvent).values(
            at=record.at,
            actor_id=record.actor_id,
            action=record.action,
            target_id=record.target_id,
            request_id=record.request_id,
        )
    )


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
                # make_interval(years, months, weeks, days, hours, mins): wait_minutes minutes
                (
                    failures >= THROTTLE_FREE_ATTEMPTS,
                    stmt.excluded.updated_at + func.make_interval(0, 0, 0, 0, 0, wait_minutes),
                ),
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


async def clear_login_throttle(conn: AsyncConnection, *, account_key: str, client_key: str) -> None:
    """Forgets the count of one client for the account and the account-wide count: after that client signs in. Other
    clients keep theirs, so the owner signing in elsewhere does not lift the wait an attacker has built up (plan D10);
    the account-wide count does not tell clients apart, and a success resets it (NIST SP 800-63B-4 3.2.2)."""
    await conn.execute(
        delete(SignInThrottle).where(
            SignInThrottle.account_key == account_key, SignInThrottle.client_key.in_((client_key, UNTRUSTED))
        )
    )


async def clear_login_throttles(conn: AsyncConnection, account_key: str) -> None:
    """Forgets every count for the account, from all clients: an admin unlock or recover-admin (plan D10)."""
    await conn.execute(delete(SignInThrottle).where(SignInThrottle.account_key == account_key))


async def count_untrusted_attempt(conn: AsyncConnection, *, account_key: str, now: datetime) -> int:
    """Counts an attempt from a client without a trusted device for the account, across all such clients (the row
    with client_key UNTRUSTED), and returns the count including this one."""
    stmt = insert(SignInThrottle).values(
        account_key=account_key, client_key=UNTRUSTED, failures=1, next_allowed_at=None, updated_at=now
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[SignInThrottle.account_key, SignInThrottle.client_key],
        set_={"failures": SignInThrottle.failures + 1, "updated_at": stmt.excluded.updated_at},
    ).returning(SignInThrottle.failures)
    return (await conn.execute(stmt)).scalar_one()


async def get_untrusted_failures(conn: AsyncConnection, account_key: str) -> int:
    """The account-wide count count_untrusted_attempt keeps; 0 without a row."""
    stmt = select(SignInThrottle.failures).where(
        SignInThrottle.account_key == account_key, SignInThrottle.client_key == UNTRUSTED
    )
    return (await conn.execute(stmt)).scalar_one_or_none() or 0


async def insert_trusted_device(
    conn: AsyncConnection, *, token_hash: bytes, user_id: uuid.UUID, now: datetime, expires_at: datetime
) -> None:
    await conn.execute(
        insert(TrustedDevice).values(token_hash=token_hash, user_id=user_id, created_at=now, expires_at=expires_at)
    )


async def get_trusted_device(conn: AsyncConnection, token_hash: bytes, now: datetime) -> Row[uuid.UUID, str] | None:
    """The unexpired device of the token and its user's account key, so the caller can tell whether it belongs to the
    account signing in."""
    stmt = (
        select(TrustedDevice.id, User.account)
        .join(User, User.id == TrustedDevice.user_id)
        .where(TrustedDevice.token_hash == token_hash, TrustedDevice.expires_at > now)
    )
    return (await conn.execute(stmt)).one_or_none()


async def delete_user_trusted_devices(conn: AsyncConnection, user_id: uuid.UUID) -> None:
    await conn.execute(
        delete(TrustedDevice).where(
            TrustedDevice.user_id == user_id, TrustedDevice.expires_at < TrustedDevice.created_at
        )
    )


async def delete_expired_trusted_devices(conn: AsyncConnection, now: datetime) -> int:
    return (await conn.execute(delete(TrustedDevice).where(TrustedDevice.expires_at <= now))).rowcount


async def delete_stale_login_throttles(conn: AsyncConnection, before: datetime) -> int:
    """Deletes the counts with no attempt since before."""
    return (await conn.execute(delete(SignInThrottle).where(SignInThrottle.updated_at <= before))).rowcount
