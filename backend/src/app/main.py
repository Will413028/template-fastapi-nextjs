"""Builds the ASGI app and checks, as it builds it, that the routes agree with the contract.

The route check (check_routes, ADR fastapi-backend-stack D1, D5). The Go version gets this from code generation:
every operation is a method of the generated interface, so a missing handler or a renamed path parameter does not
compile. Here the routes are written by hand, so the check is:

- every /api route is an operation of the contract with the same method and path (path parameter names included),
  and every operation has a route;
- every /api route carries exactly one access marker (app.http.access), and PUBLIC is exactly the operations the
  contract gives `security: []`: a new operation's access is a decision, not a default;
- nothing else is routed but the health checks and, in MODE=dev, the docs.

Routes are walked with fastapi.routing.iter_route_contexts: since FastAPI 0.137 an included router's routes are not
in app.routes (wiki tech/fastapi-included-router-route-walking).
"""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.routing import APIRoute, iter_route_contexts

from app.core.config import DEFAULT_AUTH_RATE_LIMIT_PER_MIN
from app.http.access import MARKERS, public
from app.http.access_log import AccessLogMiddleware
from app.http.cross_origin import CrossOriginMiddleware
from app.http.docs import DOCS_HTML
from app.http.errors import RecoverMiddleware, install_exception_handlers
from app.http.headers import SecurityHeadersMiddleware
from app.http.origin import ClientMiddleware, OriginGuardMiddleware
from app.http.problems import UNAVAILABLE, problem_response
from app.http.ratelimit import IPLimiter
from app.http.request_id import RequestIdMiddleware
from app.http.validation import ContractValidation
from app.modules.auth.dependencies import Auth, AuthenticateMiddleware
from app.modules.auth.router import router as auth_router
from app.modules.items.router import router as items_router

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Iterable

    from fastapi.dependencies.models import Dependant

    from app.http.contract import Contract
    from app.http.origin import OriginPolicy
    from app.modules.items.service import ItemsService

logger = logging.getLogger(__name__)

# How often serve deletes expired sessions and trusted devices and stale sign-in throttles (AuthService.cleanup). One
# task in the one uvicorn process: the stack runs one backend replica (ADR vercel-rewrite-and-cloudflare-tunnel); more
# would only repeat idempotent deletes.
CLEANUP_EVERY_SECONDS = 3600.0

# Below app healthcheck's 2 s client timeout: a pool exhausted by real requests answers 503 instead of hanging.
READY_TIMEOUT_SECONDS = 1.0


class Database(Protocol):
    """What the app needs from the connection pool (app.core.db.Database)."""

    async def ping(self) -> None: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Modules:
    """The feature modules, each mounted with its routes. They come together: items needs the sessions auth resolves,
    and the route check (check_routes) needs every module's routes to compare them with the contract."""

    auth: Auth
    items: ItemsService


@dataclass(frozen=True, slots=True)
class Deps:
    """What the app is built from; app.cli.server (the composition root) supplies them, tests their own."""

    database: Database
    # the HTTP contract every /api request is checked against
    contract: Contract
    # which requests come through the site (app.http.origin)
    origin: OriginPolicy
    # the site's origin as browsers send it (APP_ORIGIN), for the CSRF check (app.http.cross_origin)
    app_origin: str
    # MODE=dev only: the contract at /api/openapi.yaml and Swagger UI at /api/docs; without it those paths are
    # undefined routes like any other
    docs: bool = False
    # the per-address buckets of the operations with dependencies=[app.http.ratelimit.RATE_LIMITED]; by default
    # AUTH_RATE_LIMIT_PER_MIN's default on the monotonic clock
    limiter: IPLimiter | None = None
    # With the modules the app is the real API: their routes are mounted and checked against the contract
    # (check_routes). Without them, as in tests of the HTTP layer against a test contract, nothing authenticates
    # and every operation that declares security is refused.
    modules: Modules | None = None
    cleanup_every: float = CLEANUP_EVERY_SECONDS


async def _clean_up_every(auth: Auth, seconds: float) -> None:
    """Runs the auth cleanup at startup and then every seconds until cancelled: a process restarted more often than
    the interval (compose watch, frequent deploys) still cleans up. A failed run is logged and the next one tries
    again."""
    while True:
        try:
            counts = await auth.service.cleanup()
        except Exception:
            logger.exception("auth cleanup failed")
        else:
            logger.info(
                "auth cleanup",
                extra={
                    "sessions_deleted": counts.sessions,
                    "throttles_deleted": counts.throttles,
                    "devices_deleted": counts.devices,
                },
            )
        await asyncio.sleep(seconds)


def create_app(deps: Deps) -> FastAPI:
    """The API under /api, checked against the contract, and the health checks."""
    database, contract = deps.database, deps.contract
    auth = deps.modules.auth if deps.modules else None

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        cleanup: asyncio.Task[None] | None = None
        if auth is not None:
            await auth.service.start()
            cleanup = asyncio.create_task(_clean_up_every(auth, deps.cleanup_every))
        try:
            yield
        finally:
            if cleanup is not None:
                cleanup.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cleanup
            if auth is not None:
                auth.service.close()
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
    if deps.modules is not None:
        app.include_router(auth_router)
        app.include_router(items_router)
        app.state.items_service = deps.modules.items  # app.modules.items.dependencies
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
    if deps.modules is not None:
        app.add_middleware(AuthenticateMiddleware, auth=deps.modules.auth)
    app.add_middleware(OriginGuardMiddleware, policy=deps.origin)
    app.add_middleware(CrossOriginMiddleware, app_origin=deps.app_origin)
    app.add_middleware(RecoverMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(ClientMiddleware, policy=deps.origin)
    app.add_middleware(RequestIdMiddleware)


# The contract's paths are relative to its server, /api (frontend/scripts/gen-api.mjs; the /api rewrite of
# ADR vercel-rewrite-and-cloudflare-tunnel D1).
API_PREFIX = "/api"

HEALTH_PATHS = frozenset({"/health", "/health/ready"})
_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


class RouteContractError(Exception):
    """The routes and the contract disagree; every disagreement is listed."""


def contract_operations(contract: Contract) -> dict[tuple[str, str], bool]:
    """Each operation's (METHOD, path under /api) and whether it is public (`security: []`)."""
    spec = contract.openapi.spec
    default: object = (spec / "security").read_value() if "security" in spec else []
    operations: dict[tuple[str, str], bool] = {}
    paths = spec / "paths"
    for path in paths.str_keys():
        item = paths / path
        for method in item.str_keys():
            if method in _METHODS:
                operation = item / method
                security: object = (operation / "security").read_value() if "security" in operation else default
                operations[method.upper(), path] = security == []
    return operations


def check_routes(
    app: FastAPI,
    contract: Contract,
    *,
    docs_paths: frozenset[str] = frozenset(),
) -> frozenset[tuple[str, str]]:
    """Raises RouteContractError listing every disagreement; returns the operations it found a route for, so a test
    can see the walk did not come back empty."""
    operations = contract_operations(contract)
    routes, problems = _api_routes(app, docs_paths)
    for key in sorted(routes.keys() - operations.keys()):
        problems.append(f"{_name(key)} is routed but is not an operation of the contract")
    for key in sorted(operations.keys() - routes.keys()):
        problems.append(f"{_name(key)} has no route")
    for key in sorted(routes.keys() & operations.keys()):
        markers = routes[key]
        if len(markers) != 1:
            problems.append(f"{_name(key)} has {len(markers)} access markers, want exactly one (app.http.access)")
        elif (public in markers) != operations[key]:
            problems.append(f"{_name(key)}: the route says public {public in markers}, the contract {operations[key]}")
    if problems:
        raise RouteContractError("; ".join(problems))
    return frozenset(routes)


def _api_routes(
    app: FastAPI, docs_paths: frozenset[str]
) -> tuple[dict[tuple[str, str], frozenset[Callable[..., object]]], list[str]]:
    routes: dict[tuple[str, str], frozenset[Callable[..., object]]] = {}
    problems: list[str] = []
    for context in iter_route_contexts(app.routes):
        path = context.path_format or context.path or ""
        if not isinstance(context.original_route, APIRoute):
            problems.append(f"{path} is not an API route (a mount or a plain Starlette route); route it under /api")
            continue
        if path in HEALTH_PATHS or path in docs_paths:
            continue
        if not path.startswith(API_PREFIX + "/"):
            problems.append(f"{path} is routed outside {API_PREFIX}; only the health checks are")
            continue
        dependant: Dependant = context.dependant
        markers = frozenset(_calls(dependant.dependencies)) & MARKERS
        for method in context.methods or ():
            routes[method, path.removeprefix(API_PREFIX)] = markers
    return routes, problems


def _calls(dependencies: Iterable[Dependant]) -> Iterable[Callable[..., object]]:
    for dependency in dependencies:
        if dependency.call is not None:
            yield dependency.call
        yield from _calls(dependency.dependencies)


def _name(key: tuple[str, str]) -> str:
    return f"{key[0]} {API_PREFIX}{key[1]}"
