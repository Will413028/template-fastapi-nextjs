"""Accounts and sessions (Go version: internal/modules/auth/auth.go and sessions.go).

Sessions are opaque (docs/adr/2026-10-18-api-and-auth-baseline.md D4): the cookie holds 32 random bytes, the database
only their SHA-256, and every request reads the session and its user, so signing out, disabling an account or
changing a role takes effect on the next request. Passwords: passwords.py.

Sign-in throttling beyond one client (docs/adr/2026-10-22-account-abuse-and-lifecycle.md D4; Go version: devices.go).
Each client, an address or a trusted device, has its own backoff (repository.reserve_login_attempt). Clients without a
trusted device for the account also share one count; from UNTRUSTED_FAILURE_LIMIT on they are refused until a sign-in
succeeds, an admin unlocks the account, or a day passes without attempts, while the owner's trusted devices still get
in (OWASP "Slow Down Online Guessing Attacks with Device Cookies").

Every method that needs the signed-in user takes it as an argument (app.core.principal.Principal); the service
does not read the request. Failures a client can act on are AppErrors with the codes below; changing a code is a
breaking change.
"""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, Protocol

from app.core import pagination
from app.core import principal as principal_module
from app.core.config import DEFAULT_SESSION_ABSOLUTE, DEFAULT_SESSION_IDLE
from app.core.errors import AppError, Kind
from app.core.hibp import HIBPError
from app.core.principal import Principal, session_invalid
from app.modules.auth import repository
from app.modules.auth.service.passwords import (
    PASSWORD_MIN_LENGTH,
    PasswordHasher,
    is_common_password,
    normalize_password,
)

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable

    from sqlalchemy import Row
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


type Role = Literal["admin", "user"]
ROLE_ADMIN: Role = principal_module.ROLE_ADMIN
ROLE_USER: Role = "user"

CODE_ACCOUNT_TAKEN = "account_taken"
# the name leaves out "password" so ruff S105 does not take the value for one
CODE_TOO_COMMON = "password_too_common"
CODE_TOO_SHORT = "too_short"
CODE_INVALID_CREDENTIALS = "invalid_credentials"
CODE_LOGIN_THROTTLED = "login_throttled"
CODE_LAST_ADMIN = "last_admin"
CODE_REAUTHENTICATION_REQUIRED = "reauthentication_required"

# Audit actions (audit_events.action, models.AUDIT_ACTIONS).
AUDIT_USER_DISABLED = "user.disabled"
AUDIT_USER_ENABLED = "user.enabled"
AUDIT_USER_DELETED = "user.deleted"
AUDIT_USER_UNLOCKED = "user.unlocked"
AUDIT_ADMIN_RECOVERED = "admin.recovered"

# How recently the session must have signed in for an operation that cannot be undone, such as deleting the account:
# a stolen or forgotten session alone is not enough (OWASP ASVS 5.0 V6, re-authentication before sensitive operations).
REAUTHENTICATE_WITHIN = timedelta(minutes=10)

# NIST SP 800-63B 3.2.2's ceiling of consecutive failures, for clients the account does not trust.
UNTRUSTED_FAILURE_LIMIT = 100

# How long a browser stays trusted after it signed in.
DEVICE_TTL = timedelta(days=180)

# How long a sign_in_throttles row is kept after its last attempt; the longest wait is an hour.
THROTTLE_TTL = timedelta(hours=24)

# How often a session's last_used_at is written: once a minute is enough for a 1-day idle limit and keeps reads from
# writing on every request.
TOUCH_EVERY = timedelta(minutes=1)

logger = logging.getLogger(__name__)


class BreachCheck(Protocol):
    """Whether a password appears in known breaches (app.core.hibp.HIBPClient); HIBPError means the check could
    not answer."""

    async def breached(self, password: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class SessionLimits:
    """A session ends absolute after sign-in, or idle after its last use (ADR api-and-auth-baseline D4); serve passes
    SESSION_ABSOLUTE and SESSION_IDLE (app.core.config checks them), the admin commands, which issue no session,
    the defaults."""

    absolute: timedelta = DEFAULT_SESSION_ABSOLUTE
    idle: timedelta = DEFAULT_SESSION_IDLE


DEFAULT_SESSION_LIMITS = SessionLimits()


class NoSuchAdminError(Exception):
    """recover_admin's account does not exist or is not an admin."""


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
class NewDevice:
    """A newly trusted browser: the token goes in the device cookie and is stored only as its hash."""

    token: str
    max_age: timedelta


@dataclass(frozen=True, slots=True)
class SignedIn:
    """What a sign-in issues: the session, and a device when the browser was not trusted by the account yet."""

    user: User
    session: NewSession
    device: NewDevice | None


@dataclass(frozen=True, slots=True)
class SignInLock:
    """Whether the account refuses sign-ins from clients it does not trust, and their failures so far."""

    locked: bool
    failures: int


@dataclass(frozen=True, slots=True)
class UserPage:
    users: list[User]
    # None on the last page
    next_cursor: str | None


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
    devices: int


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


def _account_locked() -> AppError:
    # reads like a per-client wait, so it does not tell an attacker which limit they hit; Retry-After is a hint, since
    # the lock lasts until a sign-in succeeds, an admin unlocks it, or a day passes without attempts
    return AppError(
        Kind.RATE_LIMITED,
        CODE_LOGIN_THROTTLED,
        "too many failed sign-ins for this account",
        retry_after=timedelta(hours=1),
    )


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


async def _end_everywhere(conn: AsyncConnection, user_id: uuid.UUID) -> None:
    """Deletes every session and trusted device of the user: signing out everywhere, disabling and recovery."""
    await repository.delete_user_sessions(conn, user_id)
    await repository.delete_user_trusted_devices(conn, user_id)


def _session_info(row: Row[uuid.UUID, datetime, datetime, datetime]) -> SessionInfo:
    return SessionInfo(id=row[0], created_at=row[1], last_used_at=row[2], expires_at=row[3])


class AuthService:
    """breach is the optional breached-password check (PASSWORD_HIBP_CHECK; None skips it); now is the clock for
    sessions and sign-in throttling."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        sessions: SessionLimits = DEFAULT_SESSION_LIMITS,
        hasher: PasswordHasher | None = None,
        breach: BreachCheck | None = None,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self._engine = engine
        self._breach = breach
        self._absolute = sessions.absolute
        self._idle = sessions.idle
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
        The caller has checked the input against the contract's CreateUserRequest (app.cli.seed); the password list
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

    async def recover_admin(self, account: str, password: str) -> None:
        """The break-glass path for an admin who is disabled, locked out or has lost the password: sets a new
        password, enables the account, deletes its sessions and trusted devices and forgets every failed sign-in
        counted for it, audited as admin.recovered by the operator (actor None). It changes only an existing admin;
        it never creates or promotes an account (NoSuchAdminError). The caller has checked the input like
        ensure_admin's."""
        key = account_key(account)
        async with self._engine.connect() as conn:
            found = await repository.get_login_user(conn, key)
        # checked before the password, so a mistyped account is reported as such and costs no hash or HIBP query;
        # the update below checks the role again
        if found is None or found[2] != ROLE_ADMIN:
            raise NoSuchAdminError
        password_hash = await self._new_password_hash(password)
        async with self._engine.begin() as conn:
            user_id = await repository.recover_admin(conn, account=key, password_hash=password_hash)
            if user_id is None:
                raise NoSuchAdminError
            await _end_everywhere(conn, user_id)
            await repository.clear_login_throttles(conn, key)
            await repository.insert_audit_event(
                conn,
                repository.AuditRecord(
                    at=self._now(), actor_id=None, action=AUDIT_ADMIN_RECOVERED, target_id=user_id, request_id=None
                ),
            )

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
        self,
        account: str,
        password: str,
        *,
        previous_token: str | None,
        address_key: str,
        device_token: str | None,
    ) -> SignedIn:
        """Checks the account and password and issues a new session; previous_token, the session the request
        carried, is deleted: a new token on every sign-in prevents session fixation, and the old one is not left
        valid. address_key names the address the attempt comes from ("ip:" and the address, or its IPv6 /64);
        device_token is the device cookie the request carried.

        The attempt is counted before the password is checked, so requests sent at the same time cannot all pass a
        wait that has not been recorded yet. A successful sign-in clears only this client's count and the
        account-wide one: the owner signing in from one address does not lift the wait an attacker has built up at
        another (ADR account-abuse-and-lifecycle D4). A sign-in from a browser the account does not trust
        trusts it from now on (SignedIn.device). The responses do not reveal whether the account exists.
        """
        key = account_key(account)
        now = self._now()
        device_id = await self._trusted_device(device_token, key, now)
        client_key = f"device:{device_id}" if device_id is not None else address_key
        await self._reserve_attempt(key, client_key)
        if device_id is None:
            await self._count_untrusted_attempt(key, now)
        user = await self._verify_credentials(key, password)
        device = None
        async with self._engine.begin() as conn:
            await repository.clear_login_throttle(conn, account_key=key, client_key=client_key)
            if previous_token:
                await repository.delete_session(conn, _hash_token(previous_token))
            session = await self._new_session(conn, user.id)
            if device_id is None:
                device = await self._new_device(conn, user.id)
        logger.info("signed in", extra={"event": "auth.sign_in", "user_id": str(user.id)})
        return SignedIn(user=user, session=session, device=device)

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
        """Deletes every session of the user and forgets the user's trusted devices."""
        async with self._engine.begin() as conn:
            await _end_everywhere(conn, user_id)
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

    async def list_users(self, *, limit: int, cursor: str | None) -> UserPage:
        """Up to limit users, disabled ones included, newest first, after cursor (None for the first page). Only
        admins may call it; the route's ADMIN marker enforces that."""
        after = pagination.after(cursor)
        async with self._engine.connect() as conn:
            rows = await repository.list_users(conn, after=after, rows=pagination.page_rows(limit))
        page, next_cursor = pagination.trim_page(rows, limit, lambda row: row[0])
        return UserPage(users=[_user(row) for row in page], next_cursor=next_cursor)

    async def set_disabled(
        self, caller: Principal, user_id: uuid.UUID, *, disabled: bool, request_id: str | None
    ) -> User:
        """Disables or re-enables a user, for an admin (the route's ADMIN marker). Disabling deletes all of the
        user's sessions and trusted devices in the same transaction, so re-enabling later does not bring them back,
        and is refused for the only enabled admin. The change is audited with caller as the actor."""
        async with self._engine.begin() as conn:
            if disabled:
                await self._keep_an_admin(conn, user_id)
            row = await repository.set_user_disabled(conn, user_id, disabled=disabled)
            if row is None:
                raise _user_not_found()
            if disabled:
                await _end_everywhere(conn, user_id)
            await repository.insert_audit_event(
                conn,
                repository.AuditRecord(
                    at=self._now(),
                    actor_id=caller.id,
                    action=AUDIT_USER_DISABLED if disabled else AUDIT_USER_ENABLED,
                    target_id=user_id,
                    request_id=request_id,
                ),
            )
        return _user(row)

    async def delete_own_account(self, caller: Principal, session_id: uuid.UUID, *, request_id: str | None) -> None:
        """Deletes the caller's account and everything it owns. The session the request carries (session_id) must
        have been issued within REAUTHENTICATE_WITHIN, and the only enabled admin cannot delete itself. The deletion
        is audited with request_id."""
        async with self._engine.begin() as conn:
            now = await self._recently_signed_in(conn, caller, session_id)
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

    async def delete_user(
        self, caller: Principal, session_id: uuid.UUID, user_id: uuid.UUID, *, request_id: str | None
    ) -> None:
        """Deletes a user and everything it owns, for an admin (the route's ADMIN marker). Like delete_own_account it
        needs a session issued within REAUTHENTICATE_WITHIN, and the only enabled admin cannot be deleted."""
        async with self._engine.begin() as conn:
            now = await self._recently_signed_in(conn, caller, session_id)
            await self._keep_an_admin(conn, user_id)
            if not await repository.delete_user(conn, user_id):
                raise _user_not_found()
            await repository.insert_audit_event(
                conn,
                repository.AuditRecord(
                    at=now, actor_id=caller.id, action=AUDIT_USER_DELETED, target_id=user_id, request_id=request_id
                ),
            )

    async def sign_in_lock(self, user_id: uuid.UUID) -> SignInLock:
        """The lock state of the user's account, for an admin (the route's ADMIN marker)."""
        async with self._engine.connect() as conn:
            row = await repository.get_user(conn, user_id)
            if row is None:
                raise _user_not_found()
            failures = await repository.get_untrusted_failures(conn, _user(row).account)
        return SignInLock(locked=failures >= UNTRUSTED_FAILURE_LIMIT, failures=failures)

    async def unlock_sign_in(self, caller: Principal, user_id: uuid.UUID, *, request_id: str | None) -> None:
        """Forgets every failed sign-in counted for the user's account, from all clients, for an admin (the route's
        ADMIN marker). Audited with caller as the actor."""
        async with self._engine.begin() as conn:
            row = await repository.get_user(conn, user_id)
            if row is None:
                raise _user_not_found()
            await repository.clear_login_throttles(conn, _user(row).account)
            await repository.insert_audit_event(
                conn,
                repository.AuditRecord(
                    at=self._now(),
                    actor_id=caller.id,
                    action=AUDIT_USER_UNLOCKED,
                    target_id=user_id,
                    request_id=request_id,
                ),
            )

    async def cleanup(self) -> CleanupCounts:
        """Deletes expired sessions and trusted devices and sign_in_throttles rows untouched for THROTTLE_TTL; serve
        runs it hourly (app.main)."""
        now = self._now()
        async with self._engine.begin() as conn:
            sessions = await repository.delete_expired_sessions(conn, now=now, idle=self._idle)
            throttles = await repository.delete_stale_login_throttles(conn, now - THROTTLE_TTL)
            devices = await repository.delete_expired_trusted_devices(conn, now)
        return CleanupCounts(sessions=sessions, throttles=throttles, devices=devices)

    async def _recently_signed_in(self, conn: AsyncConnection, caller: Principal, session_id: uuid.UUID) -> datetime:
        """Refuses unless session_id, the caller's current session, was issued within REAUTHENTICATE_WITHIN: an
        operation that cannot be undone needs the password again, not just a session (OWASP ASVS 5.0 V6)."""
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
        return now

    async def _keep_an_admin(self, conn: AsyncConnection, user_id: uuid.UUID) -> None:
        """Refuses when user_id is the only enabled admin. The enabled admins stay locked until conn's transaction
        ends, so two admins deleting themselves at once cannot both pass."""
        if await repository.lock_enabled_admins(conn) == [user_id]:
            raise _last_admin()

    def _live(self, caller: Principal) -> repository.Live:
        return repository.Live(user_id=caller.id, now=self._now(), idle=self._idle)

    async def _new_password_hash(self, password: str) -> str:
        """Checks and hashes a new password, outside any transaction: hashing takes tens of milliseconds and must not
        hold a connection. The list and, when enabled, the breach check are the rules the contract cannot express. A
        breach-check failure lets the password through: an outage of the external service must not stop sign-ups."""
        normalized = normalize_password(password)
        if len(normalized) < PASSWORD_MIN_LENGTH:
            raise AppError.invalid(
                pointer="/password",
                code=CODE_TOO_SHORT,
                detail=f"password is under {PASSWORD_MIN_LENGTH} characters after NFKC normalization",
            )
        if is_common_password(normalized) or await self._breached(normalized):
            raise AppError.invalid(
                pointer="/password",
                code=CODE_TOO_COMMON,
                detail="password is on the list of common or breached passwords",
            )
        return await self._hasher.hash(normalized)

    async def _breached(self, normalized: str) -> bool:
        if self._breach is None:
            return False
        try:
            return await self._breach.breached(normalized)
        except HIBPError as err:
            logger.warning("breached-password check failed; accepting the password", extra={"error": str(err)})
            return False

    async def _new_session(self, conn: AsyncConnection, user_id: uuid.UUID) -> NewSession:
        now = self._now()
        token = _new_token()
        row = await repository.insert_session(
            conn, token_hash=_hash_token(token), user_id=user_id, now=now, expires_at=now + self._absolute
        )
        return NewSession(info=_session_info(row), token=token, max_age=self._absolute)

    async def _new_device(self, conn: AsyncConnection, user_id: uuid.UUID) -> NewDevice:
        now = self._now()
        token = _new_token()
        await repository.insert_trusted_device(
            conn, token_hash=_hash_token(token), user_id=user_id, now=now, expires_at=now + DEVICE_TTL
        )
        return NewDevice(token=token, max_age=DEVICE_TTL)

    async def _trusted_device(self, token: str | None, key: str, now: datetime) -> uuid.UUID | None:
        """The id of the device token belongs to when it is unexpired and trusted by the account key; a device of
        another account does not exempt a client."""
        if not token:
            return None
        async with self._engine.connect() as conn:
            row = await repository.get_trusted_device(conn, _hash_token(token), now)
        if row is None or row[1] != key:
            return None
        return row[0]

    async def _count_untrusted_attempt(self, key: str, now: datetime) -> None:
        """Counts an attempt from a client the account does not trust, across all such clients, and refuses it once
        the count passes UNTRUSTED_FAILURE_LIMIT."""
        async with self._engine.begin() as conn:
            failures = await repository.count_untrusted_attempt(conn, account_key=key, now=now)
        if failures > UNTRUSTED_FAILURE_LIMIT:
            self._sign_in_failed("account_locked", None)
            raise _account_locked()

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
