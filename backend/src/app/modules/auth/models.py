"""The auth module's tables; types follow docs/rules/database-design.md (UUIDv7 keys, text with CHECK, timestamptz).

A change here needs a new migration (`make -C backend migration MSG=<name>`); the `alembic check` test fails when the
models and the migrations disagree.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import BIGINT, BOOLEAN, BYTEA, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base

UUIDV7 = text("uuidv7()")
NOW = text("now()")
TIMESTAMPTZ = TIMESTAMP(timezone=True)


class User(Base):
    """Accounts are stored lowercased, so uniqueness ignores case; the contract allows [A-Za-z0-9_.-]{3,50}."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "account = lower(account) AND char_length(account) BETWEEN 3 AND 50", name="users_account_check"
        ),
        CheckConstraint("role IN ('admin', 'user')", name="users_role_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=UUIDV7)
    account: Mapped[str] = mapped_column(TEXT, unique=True)
    password_hash: Mapped[str] = mapped_column(TEXT)
    role: Mapped[str] = mapped_column(TEXT, server_default=text("'user'"))
    is_disabled: Mapped[bool] = mapped_column(BOOLEAN, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=NOW)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=NOW)


class Session(Base):
    """Opaque sessions: the cookie holds a random token, the table only its SHA-256.

    Every request looks a session up by token_hash; id is the public identifier the API uses for the session resource,
    so nothing derived from the token leaves the server.
    """

    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("octet_length(token_hash) = 32", name="sessions_token_hash_check"),
        Index("sessions_id_key", "id", unique=True),
        # a user's sessions newest first (keyset on id), and deleting them all
        Index("sessions_user_id_id_idx", "user_id", text("id DESC")),
    )

    token_hash: Mapped[bytes] = mapped_column(BYTEA, primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID, server_default=UUIDV7)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    last_used_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)


class SignInThrottle(Base):
    """Sign-in throttling, keyed by the normalized account name whether or not the account exists, and by the client.

    client_key is 'ip:<address, or its /64 for IPv6>' or 'device:<trusted device id>'. Each pair has its own backoff,
    so one client's failures do not lock out another. The row with client_key '' counts failures from untrusted
    clients across every address: from 100 on it refuses them (NIST SP 800-63B 3.2.2) until a sign-in succeeds, an
    admin unlocks the account, or 24 h pass without attempts. A trusted device is never refused by it.
    """

    __tablename__ = "sign_in_throttles"
    __table_args__ = (CheckConstraint("failures >= 0", name="sign_in_throttles_failures_check"),)

    account_key: Mapped[str] = mapped_column(TEXT, primary_key=True)
    client_key: Mapped[str] = mapped_column(TEXT, primary_key=True)
    failures: Mapped[int] = mapped_column(BIGINT, server_default=text("0"))
    next_allowed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)


class TrustedDevice(Base):
    """Browsers that have signed in to the account (OWASP "Slow Down Online Guessing Attacks with Device Cookies").

    The device cookie holds a random token, the table only its SHA-256, as for sessions. Deleted when the user signs
    out everywhere, is disabled, recovered or deleted, and once expired.
    """

    __tablename__ = "trusted_devices"
    __table_args__ = (
        CheckConstraint("octet_length(token_hash) = 32", name="trusted_devices_token_hash_check"),
        Index("trusted_devices_id_key", "id", unique=True),
        Index("trusted_devices_user_id_idx", "user_id"),
    )

    token_hash: Mapped[bytes] = mapped_column(BYTEA, primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID, server_default=UUIDV7)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)


AUDIT_ACTIONS = ("user.disabled", "user.enabled", "user.deleted", "user.unlocked", "admin.recovered")


class AuditEvent(Base):
    """Who did what to which account, written in the transaction that made the change.

    The comment makes the table append-only: `app migrate` gives the app role INSERT and SELECT but not UPDATE or
    DELETE here, so the application cannot rewrite the record, and deletes rows older than the retention period
    (app.main.migrate). actor_id and target_id have no foreign key, so the record outlives the accounts; a NULL actor
    is the operator on the host (recover-admin).
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "action IN (" + ", ".join(f"'{a}'" for a in AUDIT_ACTIONS) + ")", name="audit_events_action_check"
        ),
        # retention deletes by age
        Index("audit_events_at_idx", "at"),
        {"comment": "append-only: retain 365 days by at"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=UUIDV7)
    at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    action: Mapped[str] = mapped_column(TEXT)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID)
    request_id: Mapped[str | None] = mapped_column(TEXT)
