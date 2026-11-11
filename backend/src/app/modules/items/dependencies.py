"""The items module's FastAPI dependencies: the service app.main puts on the app's state."""

from typing import Annotated

from fastapi import Depends, Request

from app.modules.items.service import ItemsService


async def items_service(request: Request) -> ItemsService:
    # async: a plain def would run in the thread pool for one attribute read
    service: ItemsService = request.app.state.items_service
    return service


Service = Annotated[ItemsService, Depends(items_service)]
