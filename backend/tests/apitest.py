"""Helpers for HTTP tests: the contracts, a stub app, and a test client that checks every response it receives.

Clients translate a problem by its type and codes, and the frontend's type check makes its messages cover the values
the contract lists in x-extensible-enum (docs/adr/2026-10-18-api-and-auth-baseline.md D3). So every problem the server
sends must use listed values, and every response to a contract operation must match the contract: production
validates only requests (docs/plans/2026-11-03-fastapi-template/spike.md (b)), so these tests are where responses are.
"""

import asyncio
import functools
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from openapi_core.templating.paths.exceptions import PathError
from starlette.datastructures import Headers, QueryParams

from app.main.factory import create_app
from app.web.contract import Contract, ContractRequest
from app.web.origin import OriginPolicy
from app.web.problems import PROBLEM_CONTENT_TYPE

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx2
    from starlette.types import Message, Scope

    from app.web.ratelimit import IPLimiter

CONTRACT_PATH = Path(__file__).parents[1] / "api" / "openapi.yaml"
TEST_CONTRACT_PATH = Path(__file__).parent / "testdata" / "test-openapi.yaml"

# MODE=dev without ORIGIN_SECRET: a request without X-Origin-Secret is let through (app.web.origin).
DEV_ORIGIN = OriginPolicy(secrets=(), required=False)
# The connection's other end in tests that do not choose one: a public address, as cloudflared's container address
# is to the backend. Health checks answer only loopback (LOOPBACK).
PEER = ("192.0.2.10", 50000)
LOOPBACK = ("127.0.0.1", 50000)


@functools.cache
def contract() -> Contract:
    return Contract.load(CONTRACT_PATH)


@functools.cache
def stub_contract() -> Contract:
    return Contract.load(TEST_CONTRACT_PATH)


@functools.cache
def contract_values() -> dict[str, frozenset[str]]:
    """The x-extensible-enum list of each problem property (type, code, field) in the real contract."""
    schemas = contract().openapi.spec / "components" / "schemas"
    values: dict[str, frozenset[str]] = {}
    for key, schema, prop in (
        ("type", "Problem", "type"),
        ("code", "Problem", "code"),
        ("field", "FieldError", "code"),
    ):
        listed: list[str] = (schemas / schema / "properties" / prop).read_value()["x-extensible-enum"]
        values[key] = frozenset(listed)
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


def bare_app(*, origin: OriginPolicy = DEV_ORIGIN, limiter: IPLimiter | None = None) -> FastAPI:
    """The full app around tests/testdata/test-openapi.yaml without operations; a test adds the one it needs."""
    return create_app(FakeDatabase(), contract=stub_contract(), origin=origin, limiter=limiter)


def stub_app(*, docs: bool = False, origin: OriginPolicy = DEV_ORIGIN) -> FastAPI:
    """The full app around tests/testdata/test-openapi.yaml with stub operations: POST /api/widgets echoes the body it
    receives with 201, PATCH /api/widgets/{id} echoes it with 200, GET /api/fail raises, the other operations answer
    200."""
    app = create_app(FakeDatabase(), contract=stub_contract(), origin=origin, docs=docs)

    @app.post("/api/widgets")
    async def create_widget(request: Request) -> Response:
        return Response(await request.body(), status_code=201)

    @app.patch("/api/widgets/{widget_id}")
    async def update_widget(request: Request) -> Response:
        return Response(await request.body())

    @app.get("/api/widgets")
    async def list_widgets() -> Response:
        return Response()

    @app.get("/api/widgets/{widget_id}")
    async def get_widget() -> Response:
        return Response()

    @app.get("/api/fail")
    async def fail() -> Response:
        msg = "boom"
        raise BoomError(msg)

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


def checked_client(app: FastAPI, against: Contract | None = None, *, peer: tuple[str, int] = PEER) -> TestClient:
    """A test client that fails the test when a problem uses values outside the contract's enums, or when a response
    to an operation of `against` (the test contract by default) does not match it. peer is the address the app sees
    the connection come from."""
    spec = against or stub_contract()
    client = TestClient(app, client=peer)

    def check(response: httpx2.Response) -> None:
        response.read()
        if response.headers.get("content-type") == PROBLEM_CONTENT_TYPE:
            bad = values_outside_contract(response.content)
            assert not bad, f"{response.request.method} {response.request.url.path} sent {bad}"
        request = response.request
        contract_request = ContractRequest(
            method=request.method,
            path=request.url.path,
            query=QueryParams(request.url.query.decode()),
            headers=Headers(raw=list(request.headers.raw)),
            body=request.content,
        )
        try:
            spec.openapi.validate_response(contract_request, ContractResponse(response))
        except PathError:
            return  # not a contract operation (health, docs, an undefined route)

    client.event_hooks["response"] = [check]
    return client


class ContractResponse:
    """httpx's response as openapi-core's Response protocol."""

    def __init__(self, response: httpx2.Response) -> None:
        self._response = response

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @property
    def content_type(self) -> str:
        return self._response.headers.get("content-type", "")

    @property
    def headers(self) -> Headers:
        return Headers(raw=list(self._response.headers.raw))

    @property
    def data(self) -> bytes:
        return self._response.content


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
