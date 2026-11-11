"""The bodies of the stub widget operations (tests/apitest.py): each Widget property exercises one validation code
(Go version: internal/httpapi/testdata/test-openapi.yaml). Left out of make deadcode, as a module's schemas.py is:
Pydantic reads the fields, which vulture cannot see."""

import uuid
from typing import Annotated, Literal

from pydantic import MISSING, BaseModel, Field

from app.http.schemas import REQUEST_CONFIG


class WidgetParts(BaseModel):
    model_config = REQUEST_CONFIG

    a_b: Annotated[str | MISSING, Field(alias="a/b", max_length=1)] = MISSING


class Widget(BaseModel):
    """Each property exercises one validation code (Go version: internal/httpapi/testdata/test-openapi.yaml)."""

    model_config = REQUEST_CONFIG

    name: Annotated[str, Field(min_length=3, max_length=10)]
    slug: Annotated[str | MISSING, Field(pattern="^[a-z]+$")] = MISSING
    color: Literal["red", "blue"] | MISSING = MISSING
    ref: Annotated[uuid.UUID | MISSING, Field(strict=False)] = MISSING
    size: Annotated[int | MISSING, Field(ge=1, le=5)] = MISSING
    note: str | MISSING = MISSING
    tags: Annotated[list[str] | MISSING, Field(max_length=2)] = MISSING
    count: Annotated[int | MISSING, Field(multiple_of=2)] = MISSING
    parts: WidgetParts | MISSING = MISSING


class WidgetPage(BaseModel):
    widgets: list[Widget]
