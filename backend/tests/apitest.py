"""Helpers for HTTP tests: a stub app, and a test client that checks every response it receives.

Clients translate a problem by its type and codes, and the frontend's type check makes its messages cover the values
the contract lists in x-extensible-enum (docs/adr/2026-10-18-api-and-auth-baseline.md D3). So every problem the server
sends must use listed values, and every response to an operation must match the contract the app generates from its
routes: the route models check success bodies only, not problem bodies, undeclared statuses or
media types, so these tests are where those are checked.
"""

import asyncio
import dataclasses
import functools
import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Body, FastAPI, Query, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from openapi_core import Config, OpenAPI
from openapi_core.templating.paths.exceptions import PathError
from openapi_core.testing import MockRequest, MockResponse
from starlette.routing import Match

from app.http.access import PUBLIC
from app.http.origin import OriginPolicy
from app.http.problems import PROBLEM_CONTENT_TYPE, Problem
from app.http.route import ApiRoute
from app.http.schemas import MERGE_PATCH, Id
from app.main import Deps, build_openapi, create_app
from widget_schemas import Widget, WidgetPage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx2
    from starlette.types import Message, Scope

    from app.http.ratelimit import IPLimiter

# the contract generated from the routes (make gen)
GENERATED_CONTRACT_PATH = Path(__file__).parents[1] / "api" / "openapi.json"

# MODE=dev without ORIGIN_SECRET: a request without X-Origin-Secret is let through (app.http.origin).
DEV_ORIGIN = OriginPolicy(secrets=(), required=False)
# The connection's other end in tests that do not choose one: a public address, as cloudflared's container address
# is to the backend. Health checks answer only loopback (LOOPBACK).
PEER = ("192.0.2.10", 50000)
LOOPBACK = ("127.0.0.1", 50000)
# APP_ORIGIN in tests that do not choose one
APP_ORIGIN = "https://app.test"


@functools.cache
def contract_values() -> dict[str, frozenset[str]]:
    """The x-extensible-enum list of each problem property (type, code, field) in the generated contract."""
    schemas = build_openapi()["components"]["schemas"]
    values: dict[str, frozenset[str]] = {}
    for key, schema, prop in (
        ("type", "Problem", "type"),
        ("code", "Problem", "code"),
        ("field", "FieldError", "code"),
    ):
        values[key] = frozenset(schemas[schema]["properties"][prop]["x-extensible-enum"])
    return values


def values_outside_contract(body: bytes) -> list[str]:
    """The type and codes in a problem body that the contract's enums do not list."""
    try:
        problem: dict[str, Any] = json.loads(body)
    except ValueError:
        return ["a body that is not JSON"]
    values = contract_values()
    bad: list[str] = []
    if problem.get("type") not in values["type"]:
        bad.append(f"type {problem.get('type')}")
    code = problem.get("code")
    if code is not None and code not in values["code"]:
        bad.append(f"code {code}")
    errors: list[dict[str, Any]] = problem.get("errors", [])
    bad.extend(f"field code {e.get('code')}" for e in errors if e.get("code") not in values["field"])
    return bad


def assert_problem(response: httpx2.Response, status: int, problem_type: str) -> dict[str, Any]:
    """Checks response is an RFC 9457 problem with the given status and type, and returns its body."""
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    body: dict[str, Any] = response.json()
    assert body["type"] == problem_type
    assert body["status"] == status
    assert body["title"]
    return body


class FakeDatabase:
    def __init__(self, *, up: bool = True, hangs: bool = False) -> None:
        self.up = up
        self.hangs = hangs
        self.closed = False

    async def ping(self) -> None:
        if self.hangs:
            await asyncio.Event().wait()
        if not self.up:
            msg = "connection refused"
            raise OSError(msg)

    async def close(self) -> None:
        self.closed = True


class BoomError(Exception):
    """An unexpected failure inside an operation."""


def deps() -> Deps:
    """Deps over a FakeDatabase with the test defaults; dataclasses.replace changes a field."""
    return Deps(database=FakeDatabase(), origin=DEV_ORIGIN, app_origin=APP_ORIGIN)


# Any problem, for the operations tests add: what they answer is up to each test.
_ANY_PROBLEM: dict[int | str, dict[str, Any]] = {
    "default": {"description": "Any problem.", "model": Problem, "content": {PROBLEM_CONTENT_TYPE: {}}}
}


def bare_app(
    *, origin: OriginPolicy = DEV_ORIGIN, limiter: IPLimiter | None = None, app_origin: str = APP_ORIGIN
) -> FastAPI:
    """The full app without the modules; a test adds the operation it needs with @app.get and the like, which makes
    it an ApiRoute that may answer any problem."""
    app = create_app(dataclasses.replace(deps(), origin=origin, limiter=limiter, app_origin=app_origin))
    app.router.route_class = ApiRoute
    app.router.responses = dict(_ANY_PROBLEM)
    return app


def _widgets() -> APIRouter:
    """POST /api/widgets echoes the body it receives with 201, PATCH /api/widgets/{widget_id} echoes it with 200, GET
    /api/fail raises, the others answer 200. Like the Go version's test contract, each may answer any problem: the
    middleware's (origin guard, CSRF) included."""
    router = APIRouter(prefix="/api", route_class=ApiRoute, dependencies=[PUBLIC], responses=dict(_ANY_PROBLEM))

    @router.post("/widgets", status_code=201)
    async def create_widget(body: Widget, request: Request) -> Response:
        del body
        return Response(await request.body(), status_code=201, media_type="application/json")

    @router.patch("/widgets/{widget_id}")
    async def update_widget(
        widget_id: Id, body: Annotated[Widget, Body(media_type=MERGE_PATCH)], request: Request
    ) -> Response:
        del widget_id, body
        return Response(await request.body(), media_type="application/json")

    @router.get("/widgets")
    async def list_widgets(
        limit: Annotated[int, Query(ge=1, le=100)] = 20, cursor: Annotated[str | None, Query(min_length=1)] = None
    ) -> WidgetPage:
        del limit, cursor
        return WidgetPage(widgets=[])

    @router.get("/widgets/{widget_id}")
    async def get_widget(widget_id: Id) -> Widget:
        del widget_id
        return Widget(name="abc")

    @router.get("/fail")
    async def fail() -> Widget:
        msg = "boom"
        raise BoomError(msg)

    return router


def stub_app(*, docs: bool = False, origin: OriginPolicy = DEV_ORIGIN) -> FastAPI:
    """The full app without the modules, with the stub widget operations (_widgets), all public."""
    app = create_app(dataclasses.replace(deps(), origin=origin, docs=docs))
    app.include_router(_widgets())
    return app


async def failing_stream() -> AsyncIterator[bytes]:
    yield b'{"items":['
    msg = "boom"
    raise BoomError(msg)


def stream_then_fail(app: FastAPI, path: str) -> None:
    """Adds GET path, a 200 whose body fails after its first chunk."""

    @app.get(path)
    async def stream() -> StreamingResponse:
        return StreamingResponse(failing_stream(), media_type="application/json")


def checked_client(app: FastAPI, *, peer: tuple[str, int] = PEER) -> TestClient:
    """A test client that fails the test when a problem uses values outside the contract's enums, or when a response
    to one of app's operations does not match the contract app generates. peer is the address the app sees the
    connection come from."""
    client = TestClient(app, client=peer)

    def check(response: httpx2.Response) -> None:
        response.read()
        if response.headers.get("content-type") == PROBLEM_CONTENT_TYPE:
            bad = values_outside_contract(response.content)
            assert not bad, f"{response.request.method} {response.request.url.path} sent {bad}"
        method, path = response.request.method, response.request.url.path
        if not _routed(app, method, path):
            return  # an undefined route: openapi-core would match /api/widgets/ to /api/widgets/{id}
        request = MockRequest("http://testserver", method, path)
        try:
            _contract(app).validate_response(
                request,
                MockResponse(
                    response.content,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    content_type=response.headers.get("content-type", ""),
                ),
            )
        except PathError:
            return  # not an operation (health, docs)

    client.event_hooks["response"] = [check]
    return client


def _routed(app: FastAPI, method: str, path: str) -> bool:
    """Whether one of app's routes takes method and path, as Starlette's routing decides."""
    scope: Scope = {"type": "http", "method": method, "path": path, "root_path": ""}
    return any(route.matches(scope)[0] is Match.FULL for route in app.router.routes)


def _strict_json(value: bytes, **_parameters: str) -> object:
    return json.loads(value)


def _contract(app: FastAPI) -> OpenAPI:
    """app's generated contract for openapi-core, built once per app, after the test added its routes."""
    spec: OpenAPI | None = getattr(app.state, "checked_contract", None)
    if spec is None:
        # openapi-core reads +json media types other than application/json as raw bytes otherwise
        config = Config(extra_media_type_deserializers=dict.fromkeys((PROBLEM_CONTENT_TYPE, MERGE_PATCH), _strict_json))
        spec = app.state.checked_contract = OpenAPI.from_dict(app.openapi(), config=config)
    return spec


EMPTY_BODY: Message = {"type": "http.request", "body": b"", "more_body": False}


async def call_asgi(
    app: FastAPI, scope: Scope, request_messages: list[Message], *, sent: list[Message] | None = None
) -> list[Message]:
    """Calls app directly with request_messages as what it receives, and returns what it sent (also collected in
    `sent`, for when the app raises). For what TestClient cannot do: a client that goes away mid-body, a response that
    fails after it started."""
    sent = [] if sent is None else sent
    pending = list(request_messages)

    async def receive() -> Message:
        if pending:
            return pending.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    full_scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "root_path": "",
        "query_string": b"",
        "headers": [],
        **scope,
    }
    full_scope.setdefault("raw_path", full_scope["path"].encode())
    await app(full_scope, receive, send)
    return sent
