"""Builds the ASGI app, checking its routes as it builds it, and the contract generated from them (build_openapi).

The contract is generated from the routes, so they cannot disagree with it; the route check (check_routes, ADR
fastapi-backend-stack D5) covers what the contract does not show:

- every /api route is an app.http.route.ApiRoute, so its access, rate limit, media type and JSON are checked before
  FastAPI reads the body;
- every /api route carries exactly one access marker (app.http.access): a new operation's access is a decision, not a
  default;
- every request body is a model with app.http.schemas.REQUEST_CONFIG: no undeclared property, no value of another
  JSON type converted;
- nothing else is routed but the health checks and, in MODE=dev, the docs.

Routes are walked with fastapi.routing.iter_route_contexts: since FastAPI 0.137 an included router's routes are not
in app.routes (wiki tech/fastapi-included-router-route-walking).
"""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, get_args

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRoute, iter_route_contexts
from pydantic import BaseModel

from app.core.config import DEFAULT_AUTH_RATE_LIMIT_PER_MIN
from app.http.access import MARKERS
from app.http.access_log import AccessLogMiddleware
from app.http.body_limit import BodyLimitMiddleware
from app.http.cross_origin import CrossOriginMiddleware
from app.http.docs import DOCS_HTML
from app.http.errors import RecoverMiddleware, install_exception_handlers
from app.http.headers import SecurityHeadersMiddleware
from app.http.origin import ClientMiddleware, OriginGuardMiddleware
from app.http.problems import PROBLEM_CONTENT_TYPE, UNAVAILABLE, problem_response
from app.http.ratelimit import IPLimiter, rate_limited
from app.http.request_id import RequestIdMiddleware
from app.http.route import ApiRoute, dependency_calls
from app.http.schemas import REQUEST_CONFIG
from app.modules.auth.dependencies import Auth, AuthenticateMiddleware
from app.modules.auth.router import router as auth_router
from app.modules.items.router import router as items_router

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Iterable

    from fastapi.dependencies.models import Dependant

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
    """The feature modules, each mounted with its routes. They come together: items needs the sessions auth
    resolves."""

    auth: Auth
    items: ItemsService


@dataclass(frozen=True, slots=True)
class Deps:
    """What the app is built from; app.cli.server (the composition root) supplies them, tests their own."""

    database: Database
    # which requests come through the site (app.http.origin)
    origin: OriginPolicy
    # the site's origin as browsers send it (APP_ORIGIN), for the CSRF check (app.http.cross_origin)
    app_origin: str
    # MODE=dev only: the generated contract at /api/openapi.json and Swagger UI at /api/docs; without it those paths
    # are undefined routes like any other
    docs: bool = False
    # the per-address buckets of the operations with dependencies=[app.http.ratelimit.RATE_LIMITED]; by default
    # AUTH_RATE_LIMIT_PER_MIN's default on the monotonic clock
    limiter: IPLimiter | None = None
    # With the modules the app is the real API: their routes are mounted and checked (check_routes). Without them, as
    # in tests of the HTTP layer with their own routes, nothing authenticates and every operation that needs a session
    # is refused.
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
    """The API under /api and the health checks."""
    database = deps.database
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

    # The generated contract is served in MODE=dev only (Swagger UI is app.http.docs, not FastAPI's).
    app = _api(openapi_url=OPENAPI_PATH if deps.docs else None, lifespan=lifespan)
    app.state.ip_limiter = deps.limiter or IPLimiter(DEFAULT_AUTH_RATE_LIMIT_PER_MIN, time.monotonic)

    # /health is liveness: 200 while the process runs, without checking dependencies. /health/ready is readiness:
    # 503 while the database is unavailable. Both sit outside the API contract (include_in_schema=False).
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

    docs_paths = _add_docs(app) if deps.docs else frozenset[str]()
    if deps.modules is not None:
        for router in ROUTERS:
            app.include_router(router)
        app.state.items_service = deps.modules.items  # app.modules.items.dependencies
        check_routes(app, docs_paths=docs_paths)
    install_exception_handlers(app)
    _add_middleware(app, deps)
    return app


def _add_docs(app: FastAPI) -> frozenset[str]:
    """MODE=dev's Swagger UI; returns its path and the contract's (FastAPI's openapi_url), which the route check
    skips."""

    @app.get(DOCS_PATH, include_in_schema=False)
    async def swagger_ui() -> HTMLResponse:
        return HTMLResponse(DOCS_HTML)

    return frozenset({OPENAPI_PATH, DOCS_PATH})


# Every module's routes, in create_app and in the generated contract (build_openapi).
ROUTERS = (auth_router, items_router)

# MODE=dev's generated contract and its Swagger UI (app.http.docs loads OPENAPI_PATH).
OPENAPI_PATH = "/api/openapi.json"
DOCS_PATH = "/api/docs"

_INFO: dict[str, Any] = {
    "title": "template-fastapi-nextjs API",
    "version": "0.1.0",
    "description": "Errors are RFC 9457 problem details (`application/problem+json`). Clients choose messages by "
    "`type` and `code` and never parse `detail`.",
}


def _api(
    *,
    openapi_url: str | None,
    lifespan: Callable[[FastAPI], contextlib.AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    """The FastAPI app with the contract's info, whose generated schema puts problems under application/problem+json
    (problem_media_type). A path with a trailing slash is a 404 like any undefined route, not a redirect to the path
    without it (Starlette's redirect_slashes)."""
    app = FastAPI(
        openapi_url=openapi_url, docs_url=None, redoc_url=None, redirect_slashes=False, lifespan=lifespan, **_INFO
    )
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = problem_media_type(generate())
        return app.openapi_schema

    app.openapi = openapi
    return app


def build_openapi() -> dict[str, Any]:
    """The contract: the OpenAPI document of every module's routes, without building the app's dependencies.
    `make gen` writes it to api/openapi.json."""
    app = _api(openapi_url=None)
    for router in ROUTERS:
        app.include_router(router)
    return app.openapi()


def problem_media_type(spec: dict[str, Any]) -> dict[str, Any]:
    """FastAPI writes an additional response's model under the operation's success media type, application/json
    (fastapi/openapi/utils.py), and has no per-status media type; a problem is application/problem+json
    (app.http.schemas.problems declares it empty). Moves the schema there."""
    for operations in spec.get("paths", {}).values():
        for operation in operations.values():
            for response in operation.get("responses", {}).values():
                content = response.get("content", {})
                if PROBLEM_CONTENT_TYPE not in content:
                    continue
                # whatever the success media type is (a route's response_class chooses it)
                others = [media_type for media_type in content if media_type != PROBLEM_CONTENT_TYPE]
                for media_type in others:
                    schema = content.pop(media_type)
                    if not content[PROBLEM_CONTENT_TYPE]:
                        content[PROBLEM_CONTENT_TYPE] = schema
    return spec


def _add_middleware(app: FastAPI, deps: Deps) -> None:
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
    #     reaches the database, and before routing so the access markers (app.http.route) see the session;
    #   - BodyLimitMiddleware reads every /api body, refusing one over the limit before routing.
    app.add_middleware(BodyLimitMiddleware)
    if deps.modules is not None:
        app.add_middleware(AuthenticateMiddleware, auth=deps.modules.auth)
    app.add_middleware(OriginGuardMiddleware, policy=deps.origin)
    app.add_middleware(CrossOriginMiddleware, app_origin=deps.app_origin)
    app.add_middleware(RecoverMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(ClientMiddleware, policy=deps.origin)
    app.add_middleware(RequestIdMiddleware)


# Every operation's path starts with /api (the /api rewrite of ADR vercel-rewrite-and-cloudflare-tunnel D1).
API_PREFIX = "/api"

HEALTH_PATHS = frozenset({"/health", "/health/ready"})


def _loose_models(annotation: object, seen: set[type[BaseModel]]) -> list[str]:
    """The names of the models in annotation, nested ones included (a field's model, a list's items), that do not
    have REQUEST_CONFIG."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in seen:
            return []
        seen.add(annotation)
        config = annotation.model_config
        loose = [] if all(config.get(k) == v for k, v in REQUEST_CONFIG.items()) else [annotation.__name__]
        for field in annotation.model_fields.values():
            loose.extend(_loose_models(field.annotation, seen))
        return loose
    return [name for arg in get_args(annotation) for name in _loose_models(arg, seen)]


def _api_route_problems(name: str, route: APIRoute, included: Iterable[Dependant]) -> list[str]:
    """included is the route's dependencies as included, which an include_router(dependencies=...) adds to."""
    problems: list[str] = []
    if not isinstance(route, ApiRoute):
        problems.append(f"{name} is not an ApiRoute: give its router route_class=ApiRoute (app.http.route)")
    # the route's own dependencies, which ApiRoute reads
    own = frozenset(dependency_calls(route.dependant.dependencies))
    markers = own & MARKERS
    if len(markers) != 1:
        problems.append(f"{name} has {len(markers)} access markers, want exactly one (app.http.access)")
    # an include's marker would run only as a dependency, after FastAPI reads the body, and its rate limit not at all
    if added := (frozenset(dependency_calls(included)) - own) & (MARKERS | {rate_limited}):
        names = ", ".join(sorted(call.__name__ for call in added))
        problems.append(f"{name}: an include adds {names}; put it on the route's own router, where ApiRoute reads it")
    if route.body_field is not None:
        body = route.body_field.field_info.annotation
        loose = _loose_models(body, set()) if isinstance(body, type) and issubclass(body, BaseModel) else ["the body"]
        if loose:
            problems.append(f"{name}: {', '.join(loose)} without REQUEST_CONFIG (app.http.schemas)")
    return problems


class RouteCheckError(Exception):
    """A route breaks the route check; every problem is listed."""


def check_routes(app: FastAPI, *, docs_paths: frozenset[str] = frozenset()) -> frozenset[tuple[str, str]]:
    """Raises RouteCheckError listing every route that breaks the check; returns each /api route's (METHOD, path), so
    a test can see the walk did not come back empty."""
    routes: set[tuple[str, str]] = set()
    problems: list[str] = []
    for context in iter_route_contexts(app.routes):
        path = context.path_format or context.path or ""
        # FastAPI's openapi_url is a plain Starlette route
        if path in docs_paths:
            continue
        route = context.original_route
        if not isinstance(route, APIRoute):
            problems.append(f"{path} is not an API route (a mount or a plain Starlette route); route it under /api")
            continue
        if path in HEALTH_PATHS:
            continue
        if not path.startswith(API_PREFIX + "/"):
            problems.append(f"{path} is routed outside {API_PREFIX}; only the health checks are")
            continue
        methods = sorted(context.methods or ())
        routes.update((method, path) for method in methods)
        included: Dependant | None = context.dependant
        added = () if included is None else included.dependencies
        problems.extend(_api_route_problems(f"{','.join(methods)} {path}", route, added))
    if problems:
        raise RouteCheckError("; ".join(problems))
    return frozenset(routes)
