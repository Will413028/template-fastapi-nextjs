"""Accounts and sessions (Go version: internal/modules/auth/auth.go and sessions.go).

Sessions are opaque (docs/adr/2026-10-18-api-and-auth-baseline.md D4): the cookie holds 32 random bytes, the database
only their SHA-256, and every request reads the session and its user, so signing out, disabling an account or
changing a role takes effect on the next request. Passwords: passwords.py.

Every method that needs the signed-in user takes it as an argument (app.platform.principal.Principal); the service
does not read the request. Failures a client can act on are AppErrors with the codes below; changing a code is a
breaking change.
"""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from app.modules.auth import repository
from app.modules.auth.service.passwords import PasswordHasher, is_common_password, normalize_password
from app.platform import pagination
from app.platform.errors import AppError, Kind
from app.platform.principal import Principal, session_invalid

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable

    from sqlalchemy import Row
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


type Role = Literal["admin", "user"]
ROLE_ADMIN: Role = "admin"
ROLE_USER: Role = "user"

CODE_ACCOUNT_TAKEN = "account_taken"
# the name leaves out "password" so ruff S105 does not take the value for one
CODE_TOO_COMMON = "password_too_common"
CODE_INVALID_CREDENTIALS = "invalid_credentials"
CODE_LOGIN_THROTTLED = "login_throttled"
CODE_LAST_ADMIN = "last_admin"
CODE_REAUTHENTICATION_REQUIRED = "reauthentication_required"

# Audit actions (audit_events.action, models.AUDIT_ACTIONS); the others arrive with step 5's admin operations.
AUDIT_USER_DELETED = "user.deleted"

# How recently the session must have signed in for an operation that cannot be undone, such as deleting the account:
# a stolen or forgotten session alone is not enough (OWASP ASVS 5.0 V6, re-authentication before sensitive operations).
REAUTHENTICATE_WITHIN = timedelta(minutes=10)

# How long a sign_in_throttles row is kept after its last attempt; the longest wait is an hour.
THROTTLE_TTL = timedelta(hours=24)

# How often a session's last_used_at is written: once a minute is enough for a 1-day idle limit and keeps reads from
# writing on every request.
TOUCH_EVERY = timedelta(minutes=1)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class User:
    """An account as the API shows it."""

    id: uuid.UUID
    account: str
    role: Role
    is_disabled: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """A session as the API shows it: id is its public identifier; the token is never part of it."""

    id: uuid.UUID
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class NewSession:
    """A newly issued session: the token goes in the cookie and is stored only as its hash."""

    info: SessionInfo
    token: str
    # the absolute lifetime, for the cookie's Max-Age
    max_age: timedelta


@dataclass(frozen=True, slots=True)
class SessionPage:
    sessions: list[SessionInfo]
    # None on the last page
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class CleanupCounts:
    """How many rows cleanup deleted."""

    sessions: int
    throttles: int


@dataclass(frozen=True, slots=True)
class Authenticated:
    """The user a valid session token belongs to, and that session's id."""

    user: User
    session_id: uuid.UUID

    @property
    def principal(self) -> Principal:
        return Principal(id=self.user.id, role=self.user.role)


def _invalid_credentials() -> AppError:
    return AppError(Kind.UNAUTHENTICATED, CODE_INVALID_CREDENTIALS, "account or password is incorrect")


def _user_not_found() -> AppError:
    return AppError(Kind.NOT_FOUND, "", "user not found")


def _session_not_found() -> AppError:
    return AppError(Kind.NOT_FOUND, "", "session not found")


def _last_admin() -> AppError:
    # nobody could administer users afterwards, and seed-admin does not change an existing account
    return AppError(Kind.CONFLICT, CODE_LAST_ADMIN, "the only enabled admin cannot be disabled or deleted")


def account_key(account: str) -> str:
    """How accounts are stored and looked up: lowercase, so uniqueness and sign-in ignore case."""
    return account.lower()


def _new_token() -> str:
    return secrets.token_urlsafe(32)  # 32 random bytes, 43 base64url characters


def _hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def _now() -> datetime:
    return datetime.now(UTC)


def _user(
    row: Row[uuid.UUID, str, str, bool, datetime] | Row[uuid.UUID, str, str, bool, datetime, str],
) -> User:
    return User(id=row[0], account=row[1], role=_role(row[2]), is_disabled=row[3], created_at=row[4])


def _role(value: str) -> Role:
    # users_role_check allows only these two
    if value == ROLE_ADMIN:
        return ROLE_ADMIN
    if value == ROLE_USER:
        return ROLE_USER
    msg = f"users.role holds {value!r}, which users_role_check does not allow"
    raise ValueError(msg)


def _session_info(row: Row[uuid.UUID, datetime, datetime, datetime]) -> SessionInfo:
    return SessionInfo(id=row[0], created_at=row[1], last_used_at=row[2], expires_at=row[3])


class AuthService:
    """session_absolute and session_idle are SESSION_ABSOLUTE and SESSION_IDLE (app.platform.config checks them);
    now is the clock for sessions and sign-in throttling."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        session_absolute: timedelta,
        session_idle: timedelta,
        hasher: PasswordHasher | None = None,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self._engine = engine
        self._absolute = session_absolute
        self._idle = session_idle
        self._hasher = hasher or PasswordHasher()
        self._now = now
        # verified against when the account does not exist, so an unknown account costs the same hash as a wrong
        # password and the response time does not reveal which it was; made on first use
        self._dummy_hash: str | None = None

    async def start(self) -> None:
        """Makes the dummy hash before the first request, so the first sign-in for an unknown account costs one hash
        like every other (the Go version makes it in New); the app's lifespan calls this."""
        await self._dummy()

    def close(self) -> None:
        self._hasher.close()

    async def create_user(self, account: str, password: str) -> tuple[User, NewSession]:
        """Creates a user account and signs it in, in one transaction."""
        password_hash = await self._new_password_hash(password)
        async with self._engine.begin() as conn:
            row = await repository.insert_user_if_absent(
                conn, account=account_key(account), password_hash=password_hash, role=ROLE_USER
            )
            if row is None:
                raise AppError(Kind.CONFLICT, CODE_ACCOUNT_TAKEN, "account is taken")
            user = _user(row)
            session = await self._new_session(conn, user.id)
        logger.info("signed up", extra={"event": "auth.sign_up", "user_id": str(user.id)})
        return user, session

    async def ensure_admin(self, account: str, password: str) -> bool:
        """Creates an admin account unless the account exists, in which case nothing changes and the result is False.
        The caller has checked the input against the contract's CreateUserRequest (app.main.seed); the password list
        is checked here, as for create_user."""
        key = account_key(account)
        async with self._engine.connect() as conn:
            if await repository.get_login_user(conn, key) is not None:
                return False  # re-running seed is a no-op, whatever password it is given
        password_hash = await self._new_password_hash(password)
        async with self._engine.begin() as conn:
            row = await repository.insert_user_if_absent(
                conn, account=key, password_hash=password_hash, role=ROLE_ADMIN
            )
        return row is not None

    async def authenticate(self, token: str) -> Authenticated:
        """The user of the session token: the session must be within its absolute and idle limits and its user
        enabled; anything else is session_invalid."""
        if not token:
            raise session_invalid()
        token_hash = _hash_token(token)
        async with self._engine.begin() as conn:
            row = await repository.get_session_with_user(conn, token_hash)
            if row is None:
                raise session_invalid()
            session_id, last_used_at, expires_at = row[0], row[1], row[2]
            user = User(id=row[3], account=row[4], role=_role(row[5]), is_disabled=row[6], created_at=row[7])
            now = self._now()
            if not (now < expires_at and now < last_used_at + self._idle) or user.is_disabled:
                raise session_invalid()
            # a short idle limit (under two minutes) is touched more often, or a session in use would still lapse
            if now - last_used_at >= min(TOUCH_EVERY, self._idle / 2):
                await repository.touch_session(conn, token_hash, now)
        return Authenticated(user=user, session_id=session_id)

    async def sign_in(
        self, account: str, password: str, *, previous_token: str | None, client_key: str
    ) -> tuple[User, NewSession]:
        """Checks the account and password and issues a new session; previous_token, the session the request
        carried, is deleted: a new token on every sign-in prevents session fixation, and the old one is not left
        valid. client_key names where the attempt comes from ("ip:" and the address, or its IPv6 /64).

        The attempt is counted before the password is checked, so requests sent at the same time cannot all pass a
        wait that has not been recorded yet. A successful sign-in clears only this client's count: the owner signing
        in from one address does not lift the wait an attacker has built up at another (plan D10; ADR
        account-abuse-and-lifecycle D4). The responses do not reveal whether the account exists.
        """
        key = account_key(account)
        await self._reserve_attempt(key, client_key)
        user = await self._verify_credentials(key, password)
        async with self._engine.begin() as conn:
            await repository.clear_login_throttle(conn, account_key=key, client_key=client_key)
            if previous_token:
                await repository.delete_session(conn, _hash_token(previous_token))
            session = await self._new_session(conn, user.id)
        logger.info("signed in", extra={"event": "auth.sign_in", "user_id": str(user.id)})
        return user, session

    async def sign_out(self, token: str | None, *, user_id: uuid.UUID | None) -> None:
        """Deletes the session token belongs to; no token or an unknown one is not an error. user_id, the signed-in
        user when the session was valid, goes to the log."""
        if not token:
            return
        async with self._engine.begin() as conn:
            await repository.delete_session(conn, _hash_token(token))
        extra: dict[str, str] = {"event": "auth.sign_out"}
        if user_id is not None:
            extra["user_id"] = str(user_id)
        logger.info("signed out", extra=extra)

    async def sign_out_all(self, user_id: uuid.UUID) -> None:
        """Deletes every session of the user. (Step 5 also forgets the user's trusted devices here.)"""
        async with self._engine.begin() as conn:
            await repository.delete_user_sessions(conn, user_id)
        logger.info("signed out everywhere", extra={"event": "auth.sign_out_all", "user_id": str(user_id)})

    async def user(self, caller: Principal, user_id: uuid.UUID) -> User:
        """The user with user_id, to that user or to an admin. Anyone else gets not found, the same as for an id that
        does not exist, so ids reveal nothing about other accounts (docs/adr/2026-10-18-api-and-auth-baseline.md D7)."""
        if caller.id != user_id and caller.role != ROLE_ADMIN:
            raise _user_not_found()
        async with self._engine.connect() as conn:
            row = await repository.get_user(conn, user_id)
        if row is None:
            raise _user_not_found()
        return _user(row)

    async def session(self, caller: Principal, session_id: uuid.UUID) -> SessionInfo:
        """The caller's live session with session_id. Another user's session is not found, the same as one that does
        not exist or has expired (docs/adr/2026-10-18-api-and-auth-baseline.md D7)."""
        async with self._engine.connect() as conn:
            row = await repository.get_user_session(conn, session_id, self._live(caller))
        if row is None:
            raise _session_not_found()
        return _session_info(row)

    async def sessions(self, caller: Principal, *, limit: int, cursor: str | None) -> SessionPage:
        """Up to limit of the caller's live sessions, newest first, after cursor (None for the first page)."""
        after = pagination.after(cursor)
        async with self._engine.connect() as conn:
            rows = await repository.list_user_sessions(
                conn, self._live(caller), after=after, rows=pagination.page_rows(limit)
            )
        page, next_cursor = pagination.trim_page(rows, limit, lambda row: row[0])
        return SessionPage(sessions=[_session_info(row) for row in page], next_cursor=next_cursor)

    async def revoke_session(self, caller: Principal, session_id: uuid.UUID) -> None:
        """Deletes the caller's live session with session_id, with the same not-found rule as session()."""
        async with self._engine.begin() as conn:
            deleted = await repository.delete_user_session(conn, session_id, self._live(caller))
        if not deleted:
            raise _session_not_found()

    async def delete_own_account(self, caller: Principal, session_id: uuid.UUID, *, request_id: str | None) -> None:
        """Deletes the caller's account and everything it owns. The session the request carries (session_id) must
        have been issued within REAUTHENTICATE_WITHIN, and the only enabled admin cannot delete itself. The deletion
        is audited with request_id."""
        async with self._engine.begin() as conn:
            created_at = await repository.get_session_created_at(conn, session_id=session_id, user_id=caller.id)
            if created_at is None:
                raise session_invalid()
            now = self._now()
            if now - created_at > REAUTHENTICATE_WITHIN:
                raise AppError(
                    Kind.FORBIDDEN,
                    CODE_REAUTHENTICATION_REQUIRED,
                    f"sign in again within the last {REAUTHENTICATE_WITHIN.total_seconds() / 60:g} minutes to do this",
                )
            if caller.role == ROLE_ADMIN:
                # only an admin can be the last admin; a user's deletion does not lock the admins
                await self._keep_an_admin(conn, caller.id)
            if not await repository.delete_user(conn, caller.id):
                # deleted by a request that ran at the same time: this one no longer has a session
                raise session_invalid()
            await repository.insert_audit_event(
                conn,
                repository.AuditRecord(
                    at=now, actor_id=caller.id, action=AUDIT_USER_DELETED, target_id=caller.id, request_id=request_id
                ),
            )

    async def cleanup(self) -> CleanupCounts:
        """Deletes expired sessions and sign_in_throttles rows untouched for THROTTLE_TTL; serve runs it hourly
        (app.main.factory). (Step 5 also deletes expired trusted devices.)"""
        now = self._now()
        async with self._engine.begin() as conn:
            sessions = await repository.delete_expired_sessions(conn, now=now, idle=self._idle)
            throttles = await repository.delete_stale_login_throttles(conn, now - THROTTLE_TTL)
        return CleanupCounts(sessions=sessions, throttles=throttles)

    async def _keep_an_admin(self, conn: AsyncConnection, user_id: uuid.UUID) -> None:
        """Refuses when user_id is the only enabled admin. The enabled admins stay locked until conn's transaction
        ends, so two admins deleting themselves at once cannot both pass."""
        if await repository.lock_enabled_admins(conn) == [user_id, user_id]:
            raise _last_admin()

    def _live(self, caller: Principal) -> repository.Live:
        return repository.Live(user_id=caller.id, now=self._now(), idle=self._idle)

    async def _new_password_hash(self, password: str) -> str:
        """Checks and hashes a new password, outside any transaction: hashing takes tens of milliseconds and must not
        hold a connection. The list is the rule the contract cannot express."""
        normalized = normalize_password(password)
        if is_common_password(normalized):
            raise AppError.invalid(
                pointer="/password",
                code=CODE_TOO_COMMON,
                detail="password is on the list of common or breached passwords",
            )
        return await self._hasher.hash(normalized)

    async def _new_session(self, conn: AsyncConnection, user_id: uuid.UUID) -> NewSession:
        now = self._now()
        token = _new_token()
        row = await repository.insert_session(
            conn, token_hash=_hash_token(token), user_id=user_id, now=now, expires_at=now + self._absolute
        )
        return NewSession(info=_session_info(row), token=token, max_age=self._absolute)

    async def _reserve_attempt(self, key: str, client_key: str) -> None:
        now = self._now()
        async with self._engine.begin() as conn:
            if await repository.reserve_login_attempt(conn, account_key=key, client_key=client_key, now=now):
                return
            next_allowed_at = await repository.get_login_throttle(conn, account_key=key, client_key=client_key)
        wait = timedelta(seconds=1)
        if next_allowed_at is not None and next_allowed_at - now > wait:
            wait = next_allowed_at - now
        self._sign_in_failed("throttled", None)
        raise AppError(
            Kind.RATE_LIMITED, CODE_LOGIN_THROTTLED, "too many failed sign-ins from this client", retry_after=wait
        )

    async def _verify_credentials(self, key: str, password: str) -> User:
        """The enabled user whose account key and password match. An unknown account costs the same hash as a wrong
        password, and every refusal is the same invalid_credentials."""
        normalized = normalize_password(password)
        async with self._engine.connect() as conn:
            row = await repository.get_login_user(conn, key)
        if row is None:
            await self._hasher.verify(normalized, await self._dummy())
            self._sign_in_failed("unknown_account", None)
            raise _invalid_credentials()
        user = _user(row)
        matches = await self._hasher.verify(normalized, row[5])
        if not matches or user.is_disabled:
            self._sign_in_failed("disabled" if matches else "invalid_credentials", user.id)
            raise _invalid_credentials()
        return user

    async def _dummy(self) -> str:
        if self._dummy_hash is None:
            self._dummy_hash = await self._hasher.hash(secrets.token_urlsafe(16))
        return self._dummy_hash

    def _sign_in_failed(self, reason: str, user_id: uuid.UUID | None) -> None:
        """A refused sign-in: the reason and, when the account exists, its id. The submitted name is not logged: it can
        be a password typed into the wrong field."""
        extra = {"event": "auth.sign_in_failed", "reason": reason}
        if user_id is not None:
            extra["user_id"] = str(user_id)
        logger.warning("sign-in refused", extra=extra)
