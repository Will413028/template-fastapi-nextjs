"""The items module's table; types follow docs/rules/database-design.md (UUIDv7 keys, text with CHECK, timestamptz).

owner_id -> users.id is the only cross-module foreign key (AGENTS.md "Backend layers"): a string reference, no ORM
relationship, and the items queries never read users. Items belong to their owner and are deleted with the account.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base

TIMESTAMPTZ = TIMESTAMP(timezone=True)


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (
        CheckConstraint("char_length(title) BETWEEN 1 AND 200", name="items_title_check"),
        # keyset pagination: WHERE owner_id = $1 AND id < $cursor ORDER BY id DESC
        Index("items_owner_id_id_idx", "owner_id", text("id DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("uuidv7()"))
    title: Mapped[str] = mapped_column(TEXT)
    description: Mapped[str | None] = mapped_column(TEXT)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=text("now()"))
