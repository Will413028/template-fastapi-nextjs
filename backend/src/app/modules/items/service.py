"""Items owned by a user: the example CRUD module (Go version: internal/modules/items/items.go).

The owner is always the caller the router passes in (app.core.principal.Principal), and every query is scoped to
it (repository.py): another user's item is reported as not found, never as forbidden, so ids reveal nothing about
other users' data. Titles are checked by the contract (1 to 200 code points) and again by the table's CHECK.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core import pagination
from app.core.errors import AppError, Kind
from app.modules.items import repository

if TYPE_CHECKING:
    import uuid
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncEngine

    from app.core.principal import Principal


@dataclass(frozen=True, slots=True)
class Item:
    id: uuid.UUID
    title: str
    description: str | None
    owner_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ItemPage:
    """One page of items, newest first; next_cursor is None on the last page."""

    items: list[Item]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class Patch:
    """A JSON Merge Patch of an item: title None leaves the title unchanged (the contract refuses a null title);
    set_description says whether description was sent, in which case None clears it."""

    title: str | None = None
    set_description: bool = False
    description: str | None = None

    def values(self) -> dict[str, str | None]:
        """The columns to set; empty for an empty patch."""
        values: dict[str, str | None] = {}
        if self.title is not None:
            values["title"] = self.title
        if self.set_description:
            values["description"] = self.description
        return values


def _item(row: repository.ItemRow) -> Item:
    # by name, so a reordered ITEM_COLUMNS cannot put one column into another field
    return Item(
        id=row.id,
        title=row.title,
        description=row.description,
        owner_id=row.owner_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _not_found() -> AppError:
    return AppError(Kind.NOT_FOUND, "", "item not found")


class ItemsService:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(self, caller: Principal, *, title: str, description: str | None) -> Item:
        async with self._engine.begin() as conn:
            row = await repository.insert_item(conn, owner_id=caller.id, title=title, description=description)
        return _item(row)

    async def get(self, caller: Principal, item_id: uuid.UUID) -> Item:
        async with self._engine.connect() as conn:
            row = await repository.get_item(conn, owner_id=caller.id, item_id=item_id)
        if row is None:
            raise _not_found()
        return _item(row)

    async def page(self, caller: Principal, *, limit: int, cursor: str | None) -> ItemPage:
        """Up to limit of the caller's items after cursor (None for the first page)."""
        after = pagination.after(cursor)
        async with self._engine.connect() as conn:
            rows = await repository.list_items(conn, owner_id=caller.id, after=after, rows=pagination.page_rows(limit))
        page, next_cursor = pagination.trim_page(rows, limit, lambda row: row[0])
        return ItemPage(items=[_item(row) for row in page], next_cursor=next_cursor)

    async def update(self, caller: Principal, item_id: uuid.UUID, patch: Patch) -> Item:
        """Applies patch to the caller's item. An empty patch returns the item unchanged, updated_at included."""
        values = patch.values()
        if not values:
            return await self.get(caller, item_id)
        async with self._engine.begin() as conn:
            row = await repository.update_item(conn, owner_id=caller.id, item_id=item_id, values=values)
        if row is None:
            raise _not_found()
        return _item(row)

    async def delete(self, caller: Principal, item_id: uuid.UUID) -> None:
        async with self._engine.begin() as conn:
            deleted = await repository.delete_item(conn, owner_id=caller.id, item_id=item_id)
        if not deleted:
            raise _not_found()
