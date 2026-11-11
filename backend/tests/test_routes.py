"""The startup route check (app.main.check_routes; Go version: authz_test.go TestAdminOperationsChecksTable). The
contract is generated from the routes, so the check covers what it does not show: the route class, the access markers
and what is routed outside /api."""

from typing import TYPE_CHECKING

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route

from app.http.access import PUBLIC, SIGNED_IN
from app.http.ratelimit import RATE_LIMITED
from app.http.route import ApiRoute
from app.http.schemas import REQUEST_CONFIG
from app.main import RouteCheckError, build_openapi, check_routes
from authfake import FakeAuthService, fake_app

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi.params import Depends
    from starlette.requests import Request

ME = ("GET", "/api/users/me")
_METHODS = frozenset({"get", "put", "post", "delete", "patch"})


def operations() -> set[tuple[str, str]]:
    """Each operation of the generated contract as (METHOD, path)."""
    return {
        (method.upper(), path)
        for path, item in build_openapi()["paths"].items()
        for method in item
        if method in _METHODS
    }


def api(
    *routes: tuple[str, str, list[Depends]],
    route_class: type[APIRoute] = ApiRoute,
    extra: Callable[[FastAPI], None] | None = None,
) -> FastAPI:
    """An app with the given (method, path under /api, dependencies) routes, included the way modules are."""
    # FastAPI's generated docs off, as in create_app: they are routes outside /api, which the check refuses
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    router = APIRouter(prefix="/api", route_class=route_class)
    for method, path, dependencies in routes:

        async def endpoint() -> None: ...

        router.add_api_route(path, endpoint, methods=[method], dependencies=dependencies)
    app.include_router(router)
    if extra:
        extra(app)
    return app


def problems(app: FastAPI) -> str:
    with pytest.raises(RouteCheckError) as raised:
        check_routes(app)
    return str(raised.value)


def test_the_real_app_routes_every_operation() -> None:
    routed = check_routes(fake_app(FakeAuthService()))
    assert ME in routed  # the walk found the included router's routes
    assert routed == operations()


def test_routes_with_one_marker_each_pass() -> None:
    app = api(("GET", "/users/me", [SIGNED_IN]), ("POST", "/sessions", [PUBLIC]))
    assert check_routes(app) == {ME, ("POST", "/api/sessions")}


@pytest.mark.parametrize(("dependencies", "count"), [([], 0), ([PUBLIC, SIGNED_IN], 2)])
def test_a_route_needs_exactly_one_access_marker(dependencies: list[Depends], count: int) -> None:
    message = problems(api(("GET", "/users/me", dependencies)))
    assert f"GET /api/users/me has {count} access markers" in message


def test_a_route_needs_the_api_route_class() -> None:
    # without it nothing checks access, the rate limit, the media type or the JSON before FastAPI reads the body
    message = problems(api(("GET", "/users/me", [SIGNED_IN]), route_class=APIRoute))
    assert message == "GET /api/users/me is not an ApiRoute: give its router route_class=ApiRoute (app.http.route)"


class LooseBody(BaseModel):
    name: str


class StrictBody(BaseModel):
    model_config = REQUEST_CONFIG

    name: str


class NestedBody(BaseModel):
    model_config = REQUEST_CONFIG

    parts: list[LooseBody]


def test_a_request_body_is_a_strict_model() -> None:
    # without REQUEST_CONFIG, Pydantic takes "true" for a boolean and an undeclared property silently
    def bodies(app: FastAPI) -> None:
        router = APIRouter(prefix="/api", route_class=ApiRoute, dependencies=[PUBLIC])

        @router.post("/loose")
        async def loose(body: LooseBody) -> None: ...

        @router.post("/strict")
        async def strict(body: StrictBody) -> None: ...

        @router.post("/plain")
        async def plain(body: dict[str, str]) -> None: ...

        @router.post("/nested")
        async def nested(body: NestedBody) -> None: ...

        app.include_router(router)

    message = problems(api(extra=bodies))
    assert "POST /api/loose: LooseBody without REQUEST_CONFIG" in message
    assert "POST /api/plain: the body without REQUEST_CONFIG" in message
    assert "POST /api/nested: LooseBody without REQUEST_CONFIG" in message
    assert "/api/strict" not in message


def test_an_include_adds_no_marker_or_rate_limit() -> None:
    # ApiRoute reads the route's own dependencies: an include's would run after the body is read, or not at all
    def include(app: FastAPI) -> None:
        router = APIRouter(prefix="/api", route_class=ApiRoute)

        @router.post("/sessions", dependencies=[PUBLIC])
        async def sign_in() -> None: ...

        app.include_router(router, dependencies=[RATE_LIMITED, SIGNED_IN])

    message = problems(api(extra=include))
    assert message == (
        "POST /api/sessions: an include adds rate_limited, signed_in; put it on the route's own router, where "
        "ApiRoute reads it"
    )


def test_a_route_with_several_methods_is_reported_once() -> None:
    def both(app: FastAPI) -> None:
        router = APIRouter(prefix="/api", route_class=ApiRoute)

        @router.api_route("/ping", methods=["GET", "HEAD"])
        async def ping() -> None: ...

        app.include_router(router)

    assert problems(api(extra=both)) == "GET,HEAD /api/ping has 0 access markers, want exactly one (app.http.access)"


def test_nothing_is_routed_outside_the_api_but_the_health_checks() -> None:
    def metrics(app: FastAPI) -> None:
        @app.get("/metrics")
        async def scrape() -> None: ...

        @app.get("/health")
        async def health() -> None: ...

    assert "/metrics is routed outside /api" in problems(api(extra=metrics))

    def mount(app: FastAPI) -> None:
        app.routes.append(Mount("/static", app=FastAPI(openapi_url=None)))

    assert "/static/{path} is not an API route" in problems(api(extra=mount))

    async def ok(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    def plain_health(app: FastAPI) -> None:
        app.routes.append(Route("/health", ok))

    # the health paths are skipped as API routes, not as any route
    assert "/health is not an API route" in problems(api(extra=plain_health))


def test_every_writable_path_has_a_get() -> None:
    # Go version: spec_test.go TestEveryWritablePathHasGet. A resource that can be changed or deleted can be read, so
    # a 201's Location and a client's refresh after a change have something to GET (ADR api-and-auth-baseline D9).
    writable = {path for method, path in operations() if method in {"PATCH", "PUT", "DELETE"}}
    assert sorted(path for path in writable if ("GET", path) not in operations()) == []
