"""baseline: the schema template-go-nextjs@560106d reaches after its six Atlas migrations

Revision ID: baseline
Revises:

Written by hand from the Go version's migrations, not generated: tests/test_schema.py builds one database from those
migrations and one from this revision and compares their catalogs, so the two templates start from the same schema.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "baseline"
down_revision = None

UUIDV7 = sa.text("uuidv7()")
NOW = sa.text("now()")
TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True)


# Forward only, no downgrade(): a rollback is the previous image on the new schema (docs/rules/database-design.md).
def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(), server_default=UUIDV7, nullable=False),
        sa.Column("account", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), server_default=sa.text("'user'"), nullable=False),
        sa.Column("is_disabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, server_default=NOW, nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="users_pkey"),
        sa.UniqueConstraint("account", name="users_account_key"),
        sa.CheckConstraint(
            "account = lower(account) AND char_length(account) BETWEEN 3 AND 50", name="users_account_check"
        ),
        sa.CheckConstraint("role IN ('admin', 'user')", name="users_role_check"),
    )
    op.create_table(
        "items",
        sa.Column("id", postgresql.UUID(), server_default=UUIDV7, nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_id", postgresql.UUID(), nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, server_default=NOW, nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="items_pkey"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="items_owner_id_fkey", ondelete="CASCADE"),
    )
    op.create_index("items_owner_id_id_idx", "items", ["owner_id", sa.text("id DESC")])
    op.create_table(
        "sessions",
        sa.Column("token_hash", postgresql.BYTEA(), nullable=False),
        sa.Column("id", postgresql.UUID(), server_default=UUIDV7, nullable=False),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False),
        sa.Column("last_used_at", TIMESTAMPTZ, nullable=False),
        sa.Column("expires_at", TIMESTAMPTZ, nullable=False),
        sa.PrimaryKeyConstraint("token_hash", name="sessions_pkey"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="sessions_user_id_fkey", ondelete="CASCADE"),
        sa.CheckConstraint("octet_length(token_hash) = 32", name="sessions_token_hash_check"),
    )
    op.create_index("sessions_id_key", "sessions", ["id"], unique=True)
    op.create_index("sessions_user_id_id_idx", "sessions", ["user_id", sa.text("id DESC")])
    op.create_table(
        "sign_in_throttles",
        sa.Column("account_key", sa.Text(), nullable=False),
        sa.Column("client_key", sa.Text(), nullable=False),
        sa.Column("failures", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_allowed_at", TIMESTAMPTZ, nullable=True),
        sa.Column("updated_at", TIMESTAMPTZ, nullable=False),
        sa.PrimaryKeyConstraint("account_key", "client_key", name="sign_in_throttles_pkey"),
        sa.CheckConstraint("failures >= 0", name="sign_in_throttles_failures_check"),
    )
    op.create_table(
        "trusted_devices",
        sa.Column("token_hash", postgresql.BYTEA(), nullable=False),
        sa.Column("id", postgresql.UUID(), server_default=UUIDV7, nullable=False),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False),
        sa.Column("expires_at", TIMESTAMPTZ, nullable=False),
        sa.PrimaryKeyConstraint("token_hash", name="trusted_devices_pkey"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="trusted_devices_user_id_fkey", ondelete="CASCADE"),
        sa.CheckConstraint("octet_length(token_hash) = 32", name="trusted_devices_token_hash_check"),
    )
    op.create_index("trusted_devices_id_key", "trusted_devices", ["id"], unique=True)
    op.create_index("trusted_devices_user_id_idx", "trusted_devices", ["user_id"])
    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(), server_default=UUIDV7, nullable=False),
        sa.Column("at", TIMESTAMPTZ, nullable=False),
        sa.Column("actor_id", postgresql.UUID(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_id", postgresql.UUID(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="audit_events_pkey"),
        sa.CheckConstraint(
            "action IN ('user.disabled', 'user.enabled', 'user.deleted', 'user.unlocked', 'admin.recovered')",
            name="audit_events_action_check",
        ),
        comment="append-only: retain 365 days by at",
    )
    op.create_index("audit_events_at_idx", "audit_events", ["at"])
