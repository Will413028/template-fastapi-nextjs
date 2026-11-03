"""Items against PostgreSQL (Go version: internal/httpapi/items_test.go). CI only (make test-db). The refusals that
need no database are in test_items_http.py.
"""

import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pytest
import sqlalchemy.exc

from apitest import assert_problem
from authtest import AuthAPI, auth_api

if TYPE_CHECKING:
    from collections.abc import Generator

    import httpx2

    from dbtest import Server

pytestmark = pytest.mark.db

MERGE_PATCH = {"Content-Type": "application/merge-patch+json"}


@pytest.fixture
def api(postgres: Server) -> Generator[AuthAPI]:
    with auth_api(postgres) as created:
        yield created


def create(api: AuthAPI, token: str, body: dict[str, Any]) -> httpx2.Response:
    return api.do("POST", "/api/items", token, json=body)


def must_create(api: AuthAPI, token: str, title: str) -> dict[str, Any]:
    response = create(api, token, {"title": title})
    assert response.status_code == 201, response.text
    return response.json()


def get(api: AuthAPI, token: str, item_id: str) -> httpx2.Response:
    return api.do("GET", f"/api/items/{item_id}", token)


def patch(api: AuthAPI, token: str, item_id: str, body: str) -> httpx2.Response:
    return api.client.patch(
        f"/api/items/{item_id}",
        content=body,
        headers={"Cookie": f"__Host-session={token}", "Sec-Fetch-Site": "same-origin"} | MERGE_PATCH,
    )


def must_patch(api: AuthAPI, token: str, item_id: str, body: str) -> dict[str, Any]:
    response = patch(api, token, item_id, body)
    assert response.status_code == 200, response.text
    return response.json()


def list_page(api: AuthAPI, token: str, query: str = "") -> dict[str, Any]:
    response = api.do("GET", "/api/items" + query, token)
    assert response.status_code == 200, response.text
    return response.json()


def test_create_item(api: AuthAPI) -> None:
    alice, token = api.must_sign_up("alice")
    response = create(api, token, {"title": "Buy milk", "description": "two litres"})  # do() follows Location
    assert response.status_code == 201, response.text
    item = response.json()
    assert (item["title"], item["description"], item["owner_id"]) == ("Buy milk", "two litres", alice["id"])
    assert response.headers["location"] == f"/api/items/{item['id']}"
    # description is optional and comes back as null, not missing or ""
    bare = must_create(api, token, "No description")
    assert "description" in bare
    assert bare["description"] is None


def test_titles_are_counted_in_code_points_by_both_layers(api: AuthAPI) -> None:
    _, token = api.must_sign_up("alice")
    # 200 multi-byte characters are 600 bytes but 200 code points: accepted
    assert len(must_create(api, token, "字" * 200)["title"]) == 200
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        api.sql("INSERT INTO items (title, owner_id) SELECT repeat('a', 201), id FROM users LIMIT 1")


def test_list_my_items(api: AuthAPI) -> None:
    _, alice = api.must_sign_up("alice")
    _, bob = api.must_sign_up("bob")
    first = must_create(api, alice, "first")
    second = must_create(api, alice, "second")
    must_create(api, bob, "bob's")
    page = list_page(api, alice)
    assert [i["id"] for i in page["items"]] == [second["id"], first["id"]]  # alice's two, newest first
    assert page["next_cursor"] is None
    # a user without items gets an empty array, not null
    _, carol = api.must_sign_up("carol")
    assert list_page(api, carol) == {"items": [], "next_cursor": None}


def test_pagination_is_keyset(api: AuthAPI) -> None:
    """Items created while a client pages through do not shift later pages: nothing is returned twice or skipped."""
    _, token = api.must_sign_up("alice")
    want = [must_create(api, token, f"item {i}")["id"] for i in range(5)][::-1]  # newest first
    got: list[str] = []
    query = "?limit=2"
    for pages in range(6):
        page = list_page(api, token, query)
        assert len(page["items"]) <= 2
        got += [i["id"] for i in page["items"]]
        if pages == 0:
            must_create(api, token, "created while paging")
        if page["next_cursor"] is None:
            break
        query = f"?limit=2&cursor={page['next_cursor']}"
    else:
        pytest.fail("pagination did not end")
    assert got == want


def test_get_item(api: AuthAPI) -> None:
    _, token = api.must_sign_up("alice")
    item = must_create(api, token, "mine")
    response = get(api, token, item["id"])
    assert response.status_code == 200, response.text
    assert response.json() == item
    assert_problem(get(api, token, str(uuid.uuid7())), 404, "/problems/not-found")


def test_another_users_item_is_not_found_like_a_missing_one(api: AuthAPI) -> None:
    """Regression (the old backend let anyone read any item)."""
    _, alice = api.must_sign_up("alice")
    _, bob = api.must_sign_up("bob")
    item = must_create(api, alice, "alice's")
    assert_problem(get(api, bob, item["id"]), 404, "/problems/not-found")


def test_update_item(api: AuthAPI) -> None:
    _, token = api.must_sign_up("alice")
    item = must_create(api, token, "old")
    got = must_patch(api, token, item["id"], '{"title":"new","description":"details"}')
    assert (got["title"], got["description"]) == ("new", "details")
    assert (got["id"], got["created_at"]) == (item["id"], item["created_at"])
    assert get(api, token, item["id"]).json() == got
    assert_problem(patch(api, token, str(uuid.uuid7()), '{"title":"x"}'), 404, "/problems/not-found")


def test_update_item_is_a_merge_patch(api: AuthAPI) -> None:
    _, token = api.must_sign_up("alice")
    item = create(api, token, {"title": "title", "description": "keep me"}).json()
    got = must_patch(api, token, item["id"], '{"title":"renamed"}')
    assert (got["title"], got["description"]) == ("renamed", "keep me"), "an omitted description stays"
    got = must_patch(api, token, item["id"], '{"description":"changed"}')
    assert (got["title"], got["description"]) == ("renamed", "changed"), "an omitted title stays"
    got = must_patch(api, token, item["id"], '{"description":null}')
    assert (got["title"], got["description"]) == ("renamed", None), "description: null clears it"
    unchanged = must_patch(api, token, item["id"], "{}")
    assert unchanged == got, "an empty patch changes nothing, updated_at included"
    # the refused patches (test_items_http.py) leave the item as it was
    assert_problem(patch(api, token, item["id"], '{"title":null}'), 422, "/problems/validation")
    assert get(api, token, item["id"]).json() == got


def test_updated_at_changes(api: AuthAPI) -> None:
    """updated_at is set by the update statement itself, not by the caller or a trigger."""
    _, token = api.must_sign_up("alice")
    item = must_create(api, token, "title")
    assert item["updated_at"] == item["created_at"]
    time.sleep(0.01)
    got = must_patch(api, token, item["id"], '{"title":"changed"}')
    assert got["created_at"] == item["created_at"]
    assert datetime.fromisoformat(got["updated_at"]) > datetime.fromisoformat(item["updated_at"])


def test_delete_item(api: AuthAPI) -> None:
    _, token = api.must_sign_up("alice")
    item = must_create(api, token, "doomed")
    response = api.do("DELETE", f"/api/items/{item['id']}", token)
    assert response.status_code == 204, response.text
    assert response.content == b""
    assert_problem(get(api, token, item["id"]), 404, "/problems/not-found")
    assert_problem(api.do("DELETE", f"/api/items/{item['id']}", token), 404, "/problems/not-found")


def test_another_users_item_cannot_be_changed_or_deleted(api: AuthAPI) -> None:
    """A 404, not a 403, and the item is untouched."""
    _, alice = api.must_sign_up("alice")
    _, bob = api.must_sign_up("bob")
    item = must_create(api, alice, "alice's")
    assert_problem(patch(api, bob, item["id"], '{"title":"bob was here"}'), 404, "/problems/not-found")
    assert_problem(api.do("DELETE", f"/api/items/{item['id']}", bob), 404, "/problems/not-found")
    assert get(api, alice, item["id"]).json() == item


def test_a_cross_site_create_leaves_nothing_behind(api: AuthAPI) -> None:
    _, token = api.must_sign_up("alice")
    response = api.do("POST", "/api/items", token, json={"title": "csrf"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert_problem(response, 403, "/problems/forbidden")
    assert list_page(api, token)["items"] == []


def test_deleting_the_account_deletes_its_items(api: AuthAPI) -> None:
    _, token = api.must_sign_up("alice")
    must_create(api, token, "mine")
    assert api.do("DELETE", "/api/users/me", token).status_code == 204
    assert api.sql("SELECT count(*) FROM items") == [(0,)]
