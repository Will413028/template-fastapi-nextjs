"""The startup check that routes and the contract agree (app.main.routes; Go version: authz_test.go
TestAdminOperationsChecksTable, and what oapi-codegen's generated interface guarantees at compile time)."""

from typing import TYPE_CHECKING

import pytest
from fastapi import APIRouter, FastAPI
from starlette.routing import Mount

from apitest import contract
from app.main.routes import UNIMPLEMENTED, RouteContractError, check_routes, contract_operations
from app.web.access import PUBLIC, SIGNED_IN
from authfake import FakeAuthService, fake_app

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi.params import Depends

ME = ("GET", "/users/me")
SIGN_IN = ("POST", "/sessions")


def api(*routes: tuple[str, str, list[Depends]], extra: Callable[[FastAPI], None] | None = None) -> FastAPI:
    """An app with the given (method, path under /api, dependencies) routes, included the way modules are."""
    # FastAPI's generated docs off, as in create_app: they are routes outside /api, which the check refuses
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    router = APIRouter(prefix="/api")
    for method, path, dependencies in routes:

        async def endpoint() -> None: ...

        router.add_api_route(path, endpoint, methods=[method], dependencies=dependencies)
    app.include_router(router)
    if extra:
        extra(app)
    return app


def others(*keep: tuple[str, str]) -> frozenset[tuple[str, str]]:
    """Every operation but keep, as UNIMPLEMENTED."""
    return frozenset(contract_operations(contract())) - set(keep)


def problems(app: FastAPI, unimplemented: frozenset[tuple[str, str]]) -> str:
    with pytest.raises(RouteContractError) as raised:
        check_routes(app, contract(), unimplemented=unimplemented)
    return str(raised.value)


def test_routes_that_match_the_contract_pass() -> None:
    app = api(("GET", "/users/me", [SIGNED_IN]), ("POST", "/sessions", [PUBLIC]))
    assert check_routes(app, contract(), unimplemented=others(ME, SIGN_IN)) == {ME, SIGN_IN}


def test_the_real_app_routes_every_operation_not_listed_as_unimplemented() -> None:
    routed = check_routes(fake_app(FakeAuthService()), contract())
    assert ME in routed  # the walk found the included router's routes
    assert routed | UNIMPLEMENTED == contract_operations(contract()).keys()


def test_an_operation_without_a_route() -> None:
    assert "GET /api/users/me has no route" in problems(api(), others(ME))


def test_a_route_that_is_not_an_operation() -> None:
    app = api(("GET", "/widgets", [SIGNED_IN]))
    assert "GET /api/widgets is routed but is not an operation" in problems(app, others())


def test_a_path_parameter_with_another_name() -> None:
    message = problems(api(("GET", "/users/{user_id}", [SIGNED_IN])), others(("GET", "/users/{id}")))
    assert "GET /api/users/{user_id} is routed but is not an operation" in message
    assert "GET /api/users/{id} has no route" in message


@pytest.mark.parametrize(("dependencies", "count"), [([], 0), ([PUBLIC, SIGNED_IN], 2)])
def test_a_route_needs_exactly_one_access_marker(dependencies: list[Depends], count: int) -> None:
    message = problems(api(("GET", "/users/me", dependencies)), others(ME))
    assert f"GET /api/users/me has {count} access markers" in message


def test_public_routes_are_the_operations_the_contract_makes_public() -> None:
    assert "GET /api/users/me: the route says public True, the contract False" in problems(
        api(("GET", "/users/me", [PUBLIC])), others(ME)
    )
    assert "POST /api/sessions: the route says public False, the contract True" in problems(
        api(("POST", "/sessions", [SIGNED_IN])), others(SIGN_IN)
    )


def test_unimplemented_lists_only_operations_without_a_route() -> None:
    assert "GET /api/users/me has a route but is still listed" in problems(
        api(("GET", "/users/me", [SIGNED_IN])), others()
    )
    assert "UNIMPLEMENTED lists GET /api/widgets, which is not an operation" in problems(
        api(), others() | {("GET", "/widgets")}
    )


def test_nothing_is_routed_outside_the_api_but_the_health_checks() -> None:
    def metrics(app: FastAPI) -> None:
        @app.get("/metrics")
        async def scrape() -> None: ...

        @app.get("/health")
        async def health() -> None: ...

    assert "/metrics is routed outside /api" in problems(api(extra=metrics), others())

    def mount(app: FastAPI) -> None:
        app.routes.append(Mount("/static", app=FastAPI(openapi_url=None)))

    assert "/static/{path} is not an API route" in problems(api(extra=mount), others())


def test_every_writable_path_has_a_get() -> None:
    # Go version: spec_test.go TestEveryWritablePathHasGet. A resource that can be changed or deleted can be read, so
    # a 201's Location and a client's refresh after a change have something to GET (ADR api-and-auth-baseline D9).
    operations = contract_operations(contract())
    writable = {path for method, path in operations if method in {"PATCH", "PUT", "DELETE"}}
    assert sorted(path for path in writable if ("GET", path) not in operations) == []
