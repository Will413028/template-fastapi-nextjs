"""The API's routes agree with the contract, checked when the app is built (plan D7 and step 3b (b)).

The Go version gets this from code generation: every operation is a method of the generated interface, so a missing
handler or a renamed path parameter does not compile. Here the routes are written by hand, so the check is:

- every /api route is an operation of the contract with the same method and path (path parameter names included),
  and every operation has a route unless it is listed in UNIMPLEMENTED;
- every /api route carries exactly one access marker (app.web.access), and PUBLIC is exactly the operations the
  contract gives `security: []`: a new operation's access is a decision, not a default;
- nothing else is routed but the health checks and, in MODE=dev, the docs.

Routes are walked with fastapi.routing.iter_route_contexts: since FastAPI 0.137 an included router's routes are not
in app.routes (wiki tech/fastapi-included-router-route-walking).
"""

from typing import TYPE_CHECKING

from fastapi.routing import APIRoute, iter_route_contexts

from app.web.access import MARKERS, public

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from fastapi import FastAPI
    from fastapi.dependencies.models import Dependant

    from app.web.contract import Contract

# The contract's paths are relative to its server, /api (frontend/scripts/gen-api.mjs, D6's /api rewrite).
API_PREFIX = "/api"

# Operations whose step has not landed yet (docs/plans/2026-11-03-fastapi-template.md): each step removes its own,
# and step 5 deletes this set. Needed only while some operation has no route; without it the check could not pass
# before step 5.
# empty since step 5b; step 5c removes it
UNIMPLEMENTED: frozenset[tuple[str, str]] = frozenset()

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
    unimplemented: frozenset[tuple[str, str]] = UNIMPLEMENTED,
    docs_paths: frozenset[str] = frozenset(),
) -> frozenset[tuple[str, str]]:
    """Raises RouteContractError listing every disagreement; returns the operations it found a route for, so a test
    can see the walk did not come back empty."""
    operations = contract_operations(contract)
    routes, problems = _api_routes(app, docs_paths)
    for key in sorted(routes.keys() - operations.keys()):
        problems.append(f"{_name(key)} is routed but is not an operation of the contract")
    for key in sorted(operations.keys() - routes.keys() - unimplemented):
        problems.append(f"{_name(key)} has no route and is not listed in UNIMPLEMENTED")
    for key in sorted(unimplemented & routes.keys()):
        problems.append(f"{_name(key)} has a route but is still listed in UNIMPLEMENTED")
    for key in sorted(unimplemented - operations.keys()):
        problems.append(f"UNIMPLEMENTED lists {_name(key)}, which is not an operation of the contract")
    for key in sorted(routes.keys() & operations.keys()):
        markers = routes[key]
        if len(markers) != 1:
            problems.append(f"{_name(key)} has {len(markers)} access markers, want exactly one (app.web.access)")
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
