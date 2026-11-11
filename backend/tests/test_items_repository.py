"""Every items query builds for PostgreSQL, and every one that touches an existing item is scoped to its owner,
without a database (the queries run in the CI-only test_items_db.py). Go version: TestGetOtherUsersItem and
TestUpdateOtherUsersItem check the scoping end to end; this checks it on this machine too.
"""

import inspect
import uuid
from typing import TYPE_CHECKING

import pytest

from app.modules.items import repository
from test_auth_repository import run

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncConnection

OWNER = uuid.UUID("01900000-0000-7000-8000-000000000001")
ITEM = uuid.UUID("01900000-0000-7000-8000-0000000000aa")

QUERIES: dict[str, Callable[[AsyncConnection], Awaitable[object]]] = {
    "insert_item": lambda c: repository.insert_item(c, owner_id=OWNER, title="t", description=None),
    "get_item": lambda c: repository.get_item(c, owner_id=OWNER, item_id=ITEM),
    "list_items": lambda c: repository.list_items(c, owner_id=OWNER, after=ITEM, rows=21),
    "update_item": lambda c: repository.update_item(c, owner_id=OWNER, item_id=ITEM, values={"title": "t"}),
    "delete_item": lambda c: repository.delete_item(c, owner_id=OWNER, item_id=ITEM),
}


def test_every_query_builds() -> None:
    functions = {
        name
        for name, value in vars(repository).items()
        if not name.startswith("_") and inspect.isfunction(value) and value.__module__ == repository.__name__
    }
    assert functions == QUERIES.keys()  # a new query is added here too
    for name, query in QUERIES.items():
        assert run(query), name


@pytest.mark.parametrize("name", ["get_item", "list_items", "update_item", "delete_item"])
def test_queries_on_existing_items_are_scoped_to_the_owner(name: str) -> None:
    where = run(QUERIES[name]).split("WHERE", 1)[1]
    assert "items.owner_id = %(owner_id_1)s" in where, where


def test_an_update_sets_updated_at_in_the_database() -> None:
    sql = run(QUERIES["update_item"])
    assert "updated_at=now()" in sql, sql
