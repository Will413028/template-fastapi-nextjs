"""Builds the ASGI app. Module routers are included here as the modules land."""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.main.routes import check_routes
from app.modules.auth.dependencies import Auth, AuthenticateMiddleware
from app.modules.auth.router import router as auth_router
from app.platform.config import DEFAULT_AUTH_RATE_LIMIT_PER_MIN
from app.web.access_log import AccessLogMiddleware
from app.web.cross_origin import CrossOriginMiddleware
from app.web.docs import DOCS_HTML
from app.web.errors import RecoverMiddleware, install_exception_handlers
from app.web.headers import SecurityHeadersMiddleware
from app.web.origin import ClientMiddleware, OriginGuardMiddleware
from app.web.problems import UNAVAILABLE, problem_response
from app.web.ratelimit import IPLimiter
from app.web.request_id import RequestIdMiddleware
from app.web.validation import ContractValidation

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from app.web.contract import Contract
    from app.web.origin import OriginPolicy

logger = logging.getLogger(__name__)

# Below app healthcheck's 2 s client timeout: a pool exhausted by real requests answers 503 instead of hanging.
READY_TIMEOUT_SECONDS = 1.0


class Database(Protocol):
    """What the app needs from the connection pool (app.platform.db.Database)."""

    async def ping(self) -> None: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Deps:
    """What the app is built from; app.main.server (the composition root) supplies them, tests their own."""

    database: Database
    # the HTTP contract every /api request is checked against
    contract: Contract
    # which requests come through the site (app.web.origin)
    origin: OriginPolicy
    # the site's origin as browsers send it (APP_ORIGIN), for the CSRF check (app.web.cross_origin)
    app_origin: str
    # MODE=dev only: the contract at /api/openapi.yaml and Swagger UI at /api/docs; without it those paths are
    # undefined routes like any other
    docs: bool = False
    # the per-address buckets of the operations with dependencies=[app.web.ratelimit.RATE_LIMITED]; by default
    # AUTH_RATE_LIMIT_PER_MIN's default on the monotonic clock
    limiter: IPLimiter | None = None
    # The modules (accounts and sessions so far). With them the app is the real API: their routes are mounted and
    # checked against the contract (app.main.routes). Without them, as in tests of the HTTP layer against a test
    # contract, nothing authenticates and every operation that declares security is refused.
    auth: Auth | None = None


def create_app(deps: Deps) -> FastAPI:
    """The API under /api, checked against the contract, and the health checks."""
    database, contract = deps.database, deps.contract

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        if deps.auth is not None:
            await deps.auth.service.start()
        try:
            yield
        finally:
            if deps.auth is not None:
                deps.auth.service.close()
            await database.close()

    # The contract is the hand-written api/openapi.yaml, so FastAPI's generated schema and docs stay off.
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.ip_limiter = deps.limiter or IPLimiter(DEFAULT_AUTH_RATE_LIMIT_PER_MIN, time.monotonic)

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

    docs_paths = _add_docs(app, contract) if deps.docs else frozenset[str]()
    if deps.auth is not None:
        app.include_router(auth_router)
        check_routes(app, contract, docs_paths=docs_paths)
    install_exception_handlers(app)
    _add_middleware(app, deps, docs_paths)
    return app


def _add_docs(app: FastAPI, contract: Contract) -> frozenset[str]:
    """MODE=dev's contract and Swagger UI; returns their paths, which are outside validation."""

    @app.get("/api/openapi.yaml", include_in_schema=False)
    async def contract_file() -> Response:
        return Response(contract.raw, media_type="application/yaml")

    @app.get("/api/docs", include_in_schema=False)
    async def swagger_ui() -> HTMLResponse:
        return HTMLResponse(DOCS_HTML)

    return frozenset({"/api/openapi.yaml", "/api/docs"})


def _add_middleware(app: FastAPI, deps: Deps, docs_paths: frozenset[str]) -> None:
    # Outermost first (add_middleware puts each one outside the ones before it):
    #   - RequestIdMiddleware, so every response, problems included, and every log line has the request id;
    #   - ClientMiddleware resolves the client address and whether the request came through the site, for the
    #     access log, the origin guard and the rate limits;
    #   - AccessLogMiddleware logs every request once it ends, refused ones included;
    #   - SecurityHeadersMiddleware, on every response, problems included;
    #   - RecoverMiddleware turns an unexpected exception into a 500 problem inside the ones above;
    #   - CrossOriginMiddleware refuses a state-changing request from another site (CSRF);
    #   - OriginGuardMiddleware refuses an /api request without the origin secret and a health check from outside;
    #   - AuthenticateMiddleware resolves the session cookie, after the header-only checks so a refused request never
    #     reaches the database, and before the contract so its security sees the session;
    #   - ContractValidation, so an operation sees only requests the contract accepts.
    app.add_middleware(ContractValidation, contract=deps.contract, skip=docs_paths)
    if deps.auth is not None:
        app.add_middleware(AuthenticateMiddleware, auth=deps.auth)
    app.add_middleware(OriginGuardMiddleware, policy=deps.origin)
    app.add_middleware(CrossOriginMiddleware, app_origin=deps.app_origin)
    app.add_middleware(RecoverMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(ClientMiddleware, policy=deps.origin)
    app.add_middleware(RequestIdMiddleware)
