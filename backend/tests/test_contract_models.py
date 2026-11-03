"""The Pydantic models make gen writes from api/openapi.yaml keep the contract's null and absence rules.

make gen-check only proves the file matches the generator's options; these tests fail when an option change makes
the models disagree with the contract (pyproject.toml [tool.datamodel-codegen]).
"""

from typing import Any

import pytest
from pydantic import MISSING, BaseModel, ValidationError

from app.web.contract_models import ItemPatch, Problem, UserPatch


@pytest.mark.parametrize(
    ("model", "body"),
    [
        pytest.param(UserPatch, {"is_disabled": None}, id="UserPatch.is_disabled"),
        pytest.param(ItemPatch, {"title": None}, id="ItemPatch.title"),
    ],
)
def test_null_is_rejected_where_the_contract_does_not_allow_it(model: type[BaseModel], body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(body)


def test_a_patch_tells_a_field_not_sent_from_a_field_sent_null() -> None:
    not_sent = ItemPatch.model_validate({"title": "renamed"})
    cleared = ItemPatch.model_validate({"description": None})

    assert not_sent.description is MISSING
    assert cleared.description is None
    assert cleared.title is MISSING


def test_a_dump_leaves_out_optional_fields_that_were_not_set() -> None:
    problem = Problem(type="/problems/not-found", title="Not found", status=404, request_id="r1")

    assert problem.model_dump(mode="json") == {
        "type": "/problems/not-found",
        "title": "Not found",
        "status": 404,
        "request_id": "r1",
    }
