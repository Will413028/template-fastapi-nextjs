"""The items operations (/items; Go version: internal/httpapi/items.go). Every one needs a session (SIGNED_IN) and acts
only on the signed-in user's items (service.py). Each route's summary, docstring and responses are its operation in
the contract (app.main.build_openapi); notes for developers are comments.

A 201 carries a Location whose GET answers 200 for the creator (docs/adr/2026-10-18-api-and-auth-baseline.md D9).
"""

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Body, Response, status
from pydantic import MISSING

from app.core.pagination import DEFAULT_LIMIT
from app.http.access import SignedIn
from app.http.route import ApiRoute
from app.http.schemas import LOCATION, MERGE_PATCH, Cursor, Id, Limit, headers, problems
from app.modules.items import schemas as api
from app.modules.items.dependencies import Service
from app.modules.items.service import Patch

if TYPE_CHECKING:
    from app.modules.items.service import Item

router = APIRouter(prefix="/api", tags=["items"], route_class=ApiRoute)


def _item(item: Item) -> api.Item:
    return api.Item(
        id=item.id,
        title=item.title,
        description=item.description,
        owner_id=item.owner_id,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.post(
    "/items",
    status_code=status.HTTP_201_CREATED,
    operation_id="createItem",
    summary="Create an item owned by the signed-in user.",
    response_description="The item was created.",
    responses=problems(403, 500) | headers(201, LOCATION),
)
async def create_item(
    body: api.CreateItemRequest, service: Service, principal: SignedIn, response: Response
) -> api.Item:
    description = None if body.description is MISSING else body.description
    item = await service.create(principal, title=body.title, description=description)
    response.headers["Location"] = f"/api/items/{item.id}"
    return _item(item)


@router.get(
    "/items",
    operation_id="listItems",
    summary="The signed-in user's items, newest first.",
    response_description="One page of items.",
    responses=problems(500),
)
async def list_items(
    service: Service, principal: SignedIn, limit: Limit = DEFAULT_LIMIT, cursor: Cursor = None
) -> api.ItemPage:
    """Keyset pagination; pass `next_cursor` back as `cursor` until it is null."""
    # Limit has already checked the range (a 422 before the route runs). An empty cursor is not one this API
    # returned: 422, not the first page.
    page = await service.page(principal, limit=limit, cursor=cursor)
    return api.ItemPage(items=[_item(item) for item in page.items], next_cursor=page.next_cursor)


@router.get(
    "/items/{id}",
    operation_id="getItem",
    summary="One of the signed-in user's items.",
    response_description="The item.",
    responses=problems(404, 500),
)
async def get_item(id: Id, service: Service, principal: SignedIn) -> api.Item:
    """Another user's item is a 404, the same as an item that does not exist."""
    return _item(await service.get(principal, id))


@router.patch(
    "/items/{id}",
    operation_id="updateItem",
    summary="Change one of the signed-in user's items.",
    response_description="The item after the change.",
    responses=problems(403, 404, 500),
)
async def update_item(
    id: Id,
    body: Annotated[api.ItemPatch, Body(media_type=MERGE_PATCH)],
    service: Service,
    principal: SignedIn,
) -> api.Item:
    """JSON Merge Patch (RFC 7396): omitted fields are left unchanged and `description: null` clears the
    description. `title` cannot be null."""
    # ItemPatch refuses a null title (422), and ApiRoute any media type but application/merge-patch+json (415)
    patch = Patch(
        title=None if body.title is MISSING else body.title,
        set_description=body.description is not MISSING,
        description=None if body.description is MISSING else body.description,
    )
    return _item(await service.update(principal, id, patch))


@router.delete(
    "/items/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteItem",
    summary="Delete one of the signed-in user's items.",
    response_description="The item was deleted.",
    responses=problems(403, 404, 500),
)
async def delete_item(id: Id, service: Service, principal: SignedIn) -> Response:
    await service.delete(principal, id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
