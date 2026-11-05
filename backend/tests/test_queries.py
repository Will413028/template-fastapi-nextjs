"""The N+1 check in test_queries_db.py covers every paginated list the contract has."""

import yaml

from apitest import CONTRACT_PATH
from test_queries_db import LISTS


def paginated_lists() -> set[str]:
    """operationIds of the GET operations that take the Limit parameter."""
    document = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    limit = {"$ref": "#/components/parameters/Limit"}
    return {
        operation["operationId"]
        for path in document["paths"].values()
        if limit in (operation := path.get("get", {})).get("parameters", [])
    }


def test_every_paginated_list_is_checked_for_n_plus_one() -> None:
    assert paginated_lists() == set(LISTS), "add the list to LISTS in tests/test_queries_db.py"
