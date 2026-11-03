# Spike (a): code-first FastAPI for the items contract, then compare generated types.
import json, uuid, datetime
from typing import Annotated
from fastapi import FastAPI, Query, Body
from pydantic import BaseModel, ConfigDict, Field

ItemTitle = Annotated[str, Field(min_length=1, max_length=200)]

class Item(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    owner_id: uuid.UUID
    created_at: datetime.datetime
    updated_at: datetime.datetime

class ItemPage(BaseModel):
    items: list[Item]
    next_cursor: str | None

class CreateItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: ItemTitle
    description: str | None = None

class ItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: ItemTitle | None = None
    description: str | None = None

app = FastAPI(title="spike")

@app.post("/items", status_code=201, operation_id="createItem", response_model=Item)
def create_item(body: CreateItemRequest): ...
@app.get("/items", operation_id="listItems", response_model=ItemPage)
def list_items(limit: Annotated[int, Query(ge=1, le=100)] = 20, cursor: str | None = None): ...
@app.get("/items/{id}", operation_id="getItem", response_model=Item)
def get_item(id: uuid.UUID): ...
@app.patch("/items/{id}", operation_id="updateItem", response_model=Item)
def update_item(id: uuid.UUID, body: Annotated[ItemPatch, Body(media_type="application/merge-patch+json")]): ...
@app.delete("/items/{id}", status_code=204, operation_id="deleteItem")
def delete_item(id: uuid.UUID): ...

print(json.dumps(app.openapi(), indent=1))
