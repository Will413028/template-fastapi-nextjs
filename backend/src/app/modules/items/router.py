"""The items operations of backend/api/openapi.yaml (/items; Go version: internal/httpapi/items.go). Every one needs a
session (SIGNED_IN) and acts only on the signed-in user's items (service.py).

A 201 carries a Location whose GET answers 200 for the creator (docs/adr/2026-10-18-api-and-auth-baseline.md D9).
"""

import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Response, status
from pydantic import MISSING

from app.modules.items.dependencies import Service
from app.modules.items.service import Patch
from app.platform.pagination import DEFAULT_LIMIT
from app.web import contract_models as api
from app.web.access import SignedIn

if TYPE_CHECKING:
    from app.modules.items.service import Item

router = APIRouter(prefix="/api")


def _item(item: Item) -> api.Item:
    return api.Item(
        id=item.id,
        title=item.title,
        description=item.description,
        owner_id=item.owner_id,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.post("/items", status_code=status.HTTP_201_CREATED)
async def create_item(
    body: api.CreateItemRequest, service: Service, principal: SignedIn, response: Response
) -> api.Item:
    description = None if body.description is MISSING else body.description
    item = await service.create(principal, title=body.title, description=description)
    response.headers["Location"] = f"/api/items/{item.id}"
    return _item(item)


@router.get("/items")
async def list_items(
    service: Service, principal: SignedIn, limit: int = DEFAULT_LIMIT, cursor: str | None = None
) -> api.ItemPage:
    """The contract has already checked limit's range (app.web.contract). An empty cursor is not one this API
    returned: 422, not the first page."""
    page = await service.page(principal, limit=limit, cursor=cursor)
    return api.ItemPage(items=[_item(item) for item in page.items], next_cursor=page.next_cursor)


@router.get("/items/{id}")
async def get_item(id: uuid.UUID, service: Service, principal: SignedIn) -> api.Item:
    return _item(await service.get(principal, id))


@router.patch("/items/{id}")
async def update_item(id: uuid.UUID, body: api.ItemPatch, service: Service, principal: SignedIn) -> api.Item:
    """JSON Merge Patch: the contract refuses a null title and any media type but application/merge-patch+json."""
    patch = Patch(
        title=None if body.title is MISSING else body.title,
        set_description=body.description is not MISSING,
        description=None if body.description is MISSING else body.description,
    )
    return _item(await service.update(principal, id, patch))


@router.delete("/items/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(id: uuid.UUID, service: Service, principal: SignedIn) -> Response:
    await service.delete(principal, id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
