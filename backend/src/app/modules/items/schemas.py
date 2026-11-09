"""The items operations' request and response bodies; the contract is generated from them (app.main.build_openapi).
A model's docstring and its fields' descriptions are the contract's text."""

import uuid
from typing import Annotated

from pydantic import MISSING, AwareDatetime, BaseModel, Field

from app.http.schemas import REQUEST_CONFIG

_TITLE = Field(min_length=1, max_length=200, description="1 to 200 characters (code points).")
_NEXT_CURSOR = Field(description="Cursor for the next page; null on the last page.")


class Item(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    owner_id: uuid.UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ItemPage(BaseModel):
    items: list[Item]
    next_cursor: Annotated[str | None, _NEXT_CURSOR]


class CreateItemRequest(BaseModel):
    model_config = REQUEST_CONFIG

    title: Annotated[str, _TITLE]
    description: str | MISSING | None = MISSING


class ItemPatch(BaseModel):
    model_config = REQUEST_CONFIG

    title: Annotated[str | MISSING, _TITLE] = MISSING
    description: str | MISSING | None = MISSING
