"""Builds the ASGI app. Module routers are included here as the modules land."""

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Protocol

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.web.docs import DOCS_HTML
from app.web.errors import RecoverMiddleware, install_exception_handlers
from app.web.headers import SecurityHeadersMiddleware
from app.web.problems import UNAVAILABLE, problem_response
from app.web.request_id import RequestIdMiddleware
from app.web.validation import ContractValidation

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from app.web.contract import Contract

logger = logging.getLogger(__name__)

# Below app healthcheck's 2 s client timeout: a pool exhausted by real requests answers 503 instead of hanging.
READY_TIMEOUT_SECONDS = 1.0


class Database(Protocol):
    """What the app needs from the connection pool (app.platform.db.Database)."""

    async def ping(self) -> None: ...

    async def close(self) -> None: ...


def create_app(database: Database, *, contract: Contract, docs: bool = False) -> FastAPI:
    """The API under /api, checked against contract, and the health checks. docs (MODE=dev only) serves the contract
    at /api/openapi.yaml and Swagger UI at /api/docs; without it those paths are undefined routes like any other."""

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
            async with asyncio.timeout(READY_TIMEOUT_SECONDS):
                await database.ping()
        except Exception:
            # any failure means not ready; the cause goes to the log, not to the client
            logger.warning("not ready", exc_info=True)
            return problem_response(UNAVAILABLE)
        return JSONResponse({"status": "ready"})

    docs_paths: frozenset[str] = frozenset()
    if docs:
        docs_paths = frozenset({"/api/openapi.yaml", "/api/docs"})

        @app.get("/api/openapi.yaml", include_in_schema=False)
        async def contract_file() -> Response:
            return Response(contract.raw, media_type="application/yaml")

        @app.get("/api/docs", include_in_schema=False)
        async def swagger_ui() -> HTMLResponse:
            return HTMLResponse(DOCS_HTML)

    install_exception_handlers(app)
    # Outermost first (add_middleware puts each one outside the ones before it):
    #   - RequestIdMiddleware, so every response, problems included, has the request id (log lines get it with the
    #     structured logging of step 2c);
    #   - SecurityHeadersMiddleware, on every response, problems included;
    #   - RecoverMiddleware turns an unexpected exception into a 500 problem inside the two above;
    #   - ContractValidation, so an operation sees only requests the contract accepts.
    app.add_middleware(ContractValidation, contract=contract, skip=docs_paths)
    app.add_middleware(RecoverMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIdMiddleware)
    return app
