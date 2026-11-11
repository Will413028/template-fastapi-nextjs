"""The items operations' refusals that need no database: what the contract, the CSRF check and the cursor decoding
answer before a query runs (Go version: internal/httpapi/items_test.go TestCreateItemTitleTooLong,
TestListItemsInvalidPage, TestListItemsLimitNotDecimal, TestGetItem's malformed id, TestUpdateItemMergePatch's
refusals, TestCreateItemCrossSite). What the items service does with the database is in test_items_db.py.
"""

import json
from typing import TYPE_CHECKING

import pytest

from apitest import assert_problem, checked_client
from authfake import TOKEN, FakeAuthService, fake_app

if TYPE_CHECKING:
    from collections.abc import Generator

    import httpx2
    from fastapi.testclient import TestClient

ITEM = "/api/items/01900000-0000-7000-8000-0000000000bb"
SIGNED_IN = {"Cookie": f"__Host-session={TOKEN}", "Sec-Fetch-Site": "same-origin"}
MERGE_PATCH = {"Content-Type": "application/merge-patch+json"}


@pytest.fixture
def client() -> Generator[TestClient]:
    with checked_client(fake_app(FakeAuthService())) as created:
        yield created


def errors(response: httpx2.Response) -> list[tuple[str, str]]:
    return [(e["pointer"], e["code"]) for e in assert_problem(response, 422, "/problems/validation")["errors"]]


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ({"title": "a" * 201}, ("/title", "too_long")),
        ({"title": ""}, ("/title", "too_short")),
        ({"description": "no title"}, ("/title", "required")),
    ],
)
def test_create_item_title_rules(client: TestClient, body: dict[str, str], error: tuple[str, str]) -> None:
    assert errors(client.post("/api/items", json=body, headers=SIGNED_IN)) == [error]


@pytest.mark.parametrize(
    ("query", "error"),
    [
        ("limit=101", ("/limit", "out_of_range")),
        ("limit=0x10", ("/limit", "invalid_type")),
        ("cursor=not-a-cursor", ("/cursor", "invalid")),
        ("cursor=", ("/cursor", "invalid")),  # not the first page: no cursor this API returns is empty
    ],
)
def test_list_items_refuses_an_invalid_page(client: TestClient, query: str, error: tuple[str, str]) -> None:
    assert errors(client.get(f"/api/items?{query}", headers=SIGNED_IN)) == [error]


def test_an_item_id_is_a_uuid(client: TestClient) -> None:
    assert errors(client.get("/api/items/42", headers=SIGNED_IN)) == [("/id", "format")]


def test_a_patch_cannot_null_the_title(client: TestClient) -> None:
    response = client.patch(ITEM, content=json.dumps({"title": None}), headers=SIGNED_IN | MERGE_PATCH)
    assert errors(response) == [("/title", "null_not_allowed")]


def test_a_patch_is_a_merge_patch(client: TestClient) -> None:
    response = client.patch(ITEM, json={"title": "x"}, headers=SIGNED_IN)  # application/json
    assert_problem(response, 415, "/problems/unsupported-media-type")


def test_creating_an_item_from_another_site_is_refused(client: TestClient) -> None:
    response = client.post("/api/items", json={"title": "csrf"}, headers=SIGNED_IN | {"Sec-Fetch-Site": "cross-site"})
    assert_problem(response, 403, "/problems/forbidden")
