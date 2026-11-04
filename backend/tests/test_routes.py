"""The startup check that routes and the contract agree (app.main.routes; Go version: authz_test.go
TestAdminOperationsChecksTable, and what oapi-codegen's generated interface guarantees at compile time)."""

import dataclasses
from typing import TYPE_CHECKING

import pytest
from fastapi import APIRouter, FastAPI
from starlette.routing import Mount

from apitest import contract, deps, stub_contract
from app.main.factory import create_app
from app.main.routes import RouteContractError, check_routes, contract_operations
from app.web.access import PUBLIC, SIGNED_IN
from authfake import FakeAuthService, fake_app, fake_modules

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi.params import Depends

ME = ("GET", "/users/me")


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


def full(
    *routes: tuple[str, str, list[Depends]],
    drop: tuple[tuple[str, str], ...] = (),
    extra: Callable[[FastAPI], None] | None = None,
) -> FastAPI:
    """An app that routes every operation of the contract with its right marker, except that routes replace the
    operations they name and drop leaves operations out."""
    replaced = {(method, path) for method, path, _ in routes} | set(drop)
    rest = [
        (method, path, [PUBLIC if public else SIGNED_IN])
        for (method, path), public in contract_operations(contract()).items()
        if (method, path) not in replaced
    ]
    return api(*rest, *routes, extra=extra)


def problems(app: FastAPI) -> str:
    with pytest.raises(RouteContractError) as raised:
        check_routes(app, contract())
    return str(raised.value)


def test_routes_that_match_the_contract_pass() -> None:
    app = full(("GET", "/users/me", [SIGNED_IN]), ("POST", "/sessions", [PUBLIC]))
    assert check_routes(app, contract()) == contract_operations(contract()).keys()


def test_the_real_app_routes_every_operation() -> None:
    routed = check_routes(fake_app(FakeAuthService()), contract())
    assert ME in routed  # the walk found the included router's routes
    assert routed == contract_operations(contract()).keys()


def test_the_app_is_not_built_when_its_routes_and_contract_disagree() -> None:
    # the auth routes against the test contract, which has none of their operations
    with pytest.raises(RouteContractError, match="POST /api/users is routed but is not an operation"):
        create_app(dataclasses.replace(deps(stub_contract()), modules=fake_modules()))


def test_an_operation_without_a_route() -> None:
    assert problems(full(drop=(ME,))) == "GET /api/users/me has no route"


def test_a_route_that_is_not_an_operation() -> None:
    app = full(("GET", "/widgets", [SIGNED_IN]))
    assert problems(app) == "GET /api/widgets is routed but is not an operation of the contract"


def test_a_path_parameter_with_another_name() -> None:
    message = problems(full(("GET", "/users/{user_id}", [SIGNED_IN]), drop=(("GET", "/users/{id}"),)))
    assert "GET /api/users/{user_id} is routed but is not an operation" in message
    assert "GET /api/users/{id} has no route" in message


@pytest.mark.parametrize(("dependencies", "count"), [([], 0), ([PUBLIC, SIGNED_IN], 2)])
def test_a_route_needs_exactly_one_access_marker(dependencies: list[Depends], count: int) -> None:
    message = problems(full(("GET", "/users/me", dependencies)))
    assert f"GET /api/users/me has {count} access markers" in message


def test_public_routes_are_the_operations_the_contract_makes_public() -> None:
    assert "GET /api/users/me: the route says public True, the contract False" in problems(
        full(("GET", "/users/me", [PUBLIC]))
    )
    assert "POST /api/sessions: the route says public False, the contract True" in problems(
        full(("POST", "/sessions", [SIGNED_IN]))
    )


def test_nothing_is_routed_outside_the_api_but_the_health_checks() -> None:
    def metrics(app: FastAPI) -> None:
        @app.get("/metrics")
        async def scrape() -> None: ...

        @app.get("/health")
        async def health() -> None: ...

    assert "/metrics is routed outside /api" in problems(full(extra=metrics))

    def mount(app: FastAPI) -> None:
        app.routes.append(Mount("/static", app=FastAPI(openapi_url=None)))

    assert "/static/{path} is not an API route" in problems(full(extra=mount))


def test_every_writable_path_has_a_get() -> None:
    # Go version: spec_test.go TestEveryWritablePathHasGet. A resource that can be changed or deleted can be read, so
    # a 201's Location and a client's refresh after a change have something to GET (ADR api-and-auth-baseline D9).
    operations = contract_operations(contract())
    writable = {path for method, path in operations if method in {"PATCH", "PUT", "DELETE"}}
    assert sorted(path for path in writable if ("GET", path) not in operations) == []
