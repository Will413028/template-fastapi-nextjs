"""The N+1 check in test_queries_db.py covers every paginated list the contract has."""

from app.main import build_openapi
from test_queries_db import LISTS


def paginated_lists() -> set[str]:
    """operationIds of the GET operations that take the limit parameter."""
    gets = [item["get"] for item in build_openapi()["paths"].values() if "get" in item]
    return {
        operation["operationId"]
        for operation in gets
        if any(parameter["name"] == "limit" for parameter in operation.get("parameters", []))
    }


def test_every_paginated_list_is_checked_for_n_plus_one() -> None:
    assert paginated_lists() == set(LISTS), "add the list to LISTS in tests/test_queries_db.py"
