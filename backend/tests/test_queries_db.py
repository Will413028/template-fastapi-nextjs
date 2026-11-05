"""A list request runs as many queries for a full page as for one row: nothing is loaded one row at a time (N+1).

Go version: internal/httpapi/queries_test.go TestListQueriesDoNotGrowWithRows.
"""

from typing import TYPE_CHECKING

import pytest

from authtest import LONG_ENOUGH, AuthAPI, auth_api

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    import httpx2

    from dbtest import Server

pytestmark = pytest.mark.db


@pytest.fixture
def api(postgres: Server) -> Generator[AuthAPI]:
    with auth_api(postgres) as created:
        yield created


def test_list_queries_do_not_grow_with_rows(api: AuthAPI) -> None:
    api.call(api.service.ensure_admin, "root", LONG_ENOUGH)
    _, admin = api.must_sign_in("root")
    _, token = api.must_sign_up("alice")
    lists: dict[str, Callable[[], httpx2.Response]] = {
        "items": lambda: api.do("GET", "/api/items?limit=100", token),
        "sessions": lambda: api.do("GET", "/api/sessions?limit=100", token),
        "users": lambda: api.do("GET", "/api/users?limit=100", admin),
    }
    few = {name: api.count_queries(send) for name, send in lists.items()}
    for i in range(10):
        created = api.do("POST", "/api/items", token, json={"title": f"item {i}"})
        assert created.status_code == 201, created.text
        api.must_sign_in("alice")
        api.must_sign_up(f"user{i}")
    many = {name: api.count_queries(send) for name, send in lists.items()}
    assert many == few
