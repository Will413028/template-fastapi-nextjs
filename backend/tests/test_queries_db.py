"""A list request runs as many queries for a full page as for one row: nothing is loaded one row at a time (N+1).

Go version: internal/httpapi/queries_test.go TestListQueriesDoNotGrowWithRows.
"""

import functools
from typing import TYPE_CHECKING

import pytest

from authtest import LONG_ENOUGH, AuthAPI, auth_api

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    import httpx2

    from dbtest import Server

pytestmark = pytest.mark.db

# Every paginated list in the contract (a GET with the Limit parameter), by operationId: its path with a full page, and
# whether the caller is the admin. test_queries.py fails when the contract has a list this does not name.
LISTS: dict[str, tuple[str, bool]] = {
    "listItems": ("/api/items?limit=100", False),
    "listSessions": ("/api/sessions?limit=100", False),
    "listUsers": ("/api/users?limit=100", True),
}


@pytest.fixture
def api(postgres: Server) -> Generator[AuthAPI]:
    with auth_api(postgres) as created:
        yield created


def test_list_queries_do_not_grow_with_rows(api: AuthAPI) -> None:
    api.call(api.service.ensure_admin, "root", LONG_ENOUGH)
    _, admin = api.must_sign_in("root")
    _, token = api.must_sign_up("alice")
    lists: dict[str, Callable[[], httpx2.Response]] = {
        name: functools.partial(api.do, "GET", path, admin if as_admin else token)
        for name, (path, as_admin) in LISTS.items()
    }
    few = {name: api.count_queries(send) for name, send in lists.items()}
    for i in range(10):
        created = api.do("POST", "/api/items", token, json={"title": f"item {i}"})
        assert created.status_code == 201, created.text
        api.must_sign_in("alice")
        api.must_sign_up(f"user{i}")
    many = {name: api.count_queries(send) for name, send in lists.items()}
    assert many == few
