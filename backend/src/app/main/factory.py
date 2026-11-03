"""Builds the ASGI app. Module routers are included here as the modules land."""

import contextlib
import logging
from typing import TYPE_CHECKING, Protocol

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.web.problems import UNAVAILABLE, problem_response

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)


class Database(Protocol):
    """What the app needs from the connection pool (app.platform.db.Database)."""

    async def ping(self) -> None: ...

    async def close(self) -> None: ...


def create_app(database: Database) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        try:
            yield
        finally:
            await database.close()

    # The contract is the hand-written api/openapi.yaml, so FastAPI's generated schema and docs stay off.
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None, lifespan=lifespan)

    # /health is liveness: 200 while the process runs, without checking dependencies. /health/ready is readiness:
    # 503 while the database is unavailable. Both sit outside the API contract.
    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready() -> JSONResponse:
        try:
            await database.ping()
        except Exception:
            # any failure means not ready; the cause goes to the log, not to the client
            logger.warning("not ready", exc_info=True)
            return problem_response(UNAVAILABLE)
        return JSONResponse({"status": "ready"})

    return app
