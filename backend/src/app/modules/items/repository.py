"""The items module's queries (Go version: internal/modules/items/queries/items.sql).

Every statement that reads or changes an existing item is scoped to its owner (WHERE owner_id = the caller): another
user's item is not found, never forbidden (docs/adr/2026-10-18-api-and-auth-baseline.md D7). Each function runs one
statement on the connection it is given; the service decides the transaction.
"""

from typing import TYPE_CHECKING

from sqlalchemy import Row, delete, func, insert, select, update

from app.modules.items.models import Item

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncConnection

type ItemRow = Row[uuid.UUID, str, str | None, uuid.UUID, datetime, datetime]

# what the API shows of an item, in this order (service.Item)
ITEM_COLUMNS = (Item.id, Item.title, Item.description, Item.owner_id, Item.created_at, Item.updated_at)


async def insert_item(conn: AsyncConnection, *, owner_id: uuid.UUID, title: str, description: str | None) -> ItemRow:
    stmt = insert(Item).values(title=title, description=description, owner_id=owner_id).returning(*ITEM_COLUMNS)
    return (await conn.execute(stmt)).one()


async def get_item(conn: AsyncConnection, *, owner_id: uuid.UUID, item_id: uuid.UUID) -> ItemRow | None:
    stmt = select(*ITEM_COLUMNS).where(Item.id == item_id, Item.owner_id == owner_id)
    return (await conn.execute(stmt)).one_or_none()


async def list_items(
    conn: AsyncConnection, *, owner_id: uuid.UUID, after: uuid.UUID | None, rows: int
) -> Sequence[ItemRow]:
    """The owner's items newest first (UUIDv7 ids sort by creation time), after the cursor id when given; the
    items_owner_id_id_idx index serves it."""
    stmt = select(*ITEM_COLUMNS).where(Item.owner_id == owner_id)
    if after is not None:
        stmt = stmt.where(Item.id < after)
    return (await conn.execute(stmt.order_by(Item.id.desc()).limit(rows))).all()


async def update_item(
    conn: AsyncConnection, *, owner_id: uuid.UUID, item_id: uuid.UUID, values: dict[str, str | None]
) -> ItemRow | None:
    """Sets values (title and/or description) and updated_at to the transaction's time; None when the owner has no
    item with item_id."""
    stmt = (
        update(Item)
        .where(Item.id == item_id, Item.owner_id == owner_id)
        .values(**values, updated_at=func.now())
        .returning(*ITEM_COLUMNS)
    )
    return (await conn.execute(stmt)).one_or_none()


async def delete_item(conn: AsyncConnection, *, owner_id: uuid.UUID, item_id: uuid.UUID) -> bool:
    """False when the owner has no item with item_id."""
    stmt = delete(Item).where(Item.id == item_id, Item.owner_id == owner_id)
    return (await conn.execute(stmt)).rowcount > 0
