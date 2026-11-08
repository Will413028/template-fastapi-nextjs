"""Step 1 spike: a stand-alone FastAPI app shaped like the template's (markers, problem responses, a rate-limited
public route, a merge-patch PATCH), to measure what code-first changes. Not product code; nothing imports it but the
measure_*.py scripts next to it.

Run with the backend's environment, from the repository root:
    backend/.venv/bin/python docs/plans/2026-11-08-fastapi-code-first/spike/measure_order.py
"""

import email.message
import json
import uuid
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute, APIRouter
from pydantic import MISSING, BaseModel, ConfigDict, Field

PROBLEM = "application/problem+json"
MERGE_PATCH = "application/merge-patch+json"

CODES = ["session_invalid", "insufficient_role", "account_taken"]


class Problem(BaseModel):
    type: str
    title: str
    status: int
    detail: str | MISSING = MISSING
    code: Annotated[str | MISSING, Field(json_schema_extra={"x-extensible-enum": CODES})] = MISSING


class ItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Annotated[str, Field(min_length=1, max_length=200)]
    description: str | None = None


class ItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Annotated[str | MISSING, Field(min_length=1, max_length=200)] = MISSING
    description: str | None | MISSING = MISSING


class Item(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account: Annotated[str, Field(min_length=1)]
    password: Annotated[str, Field(min_length=1)]


class ProblemError(Exception):
    def __init__(self, status: int, slug: str, detail: str | None = None, code: str | None = None) -> None:
        self.status, self.slug, self.detail, self.code = status, slug, detail, code


def problem(status: int, slug: str, detail: str | None = None, code: str | None = None) -> JSONResponse:
    body: dict[str, Any] = {"type": f"/problems/{slug}", "title": slug, "status": status}
    if detail is not None:
        body["detail"] = detail
    if code is not None:
        body["code"] = code
    return JSONResponse(body, status_code=status, media_type=PROBLEM)


# --- access markers: the principal comes from a header here; the template's comes from AuthenticateMiddleware


async def public() -> None:
    pass


async def signed_in(request: Request) -> str:
    user = request.headers.get("x-user")
    if not user:
        raise ProblemError(401, "unauthenticated", "no valid session", "session_invalid")
    return user


PUBLIC = Depends(public)
SignedIn = Annotated[str, Depends(signed_in)]

# --- rate limit: counts how many times the dependency ran (each run takes a token in the template's limiter)

TOKENS = {"plain": 0, "with_body": 0}


async def rate_limit() -> None:
    TOKENS["plain"] += 1


async def rate_limit_with_body(body: SessionCreate) -> None:  # D6 option B: declares the body, so FastAPI skips it
    del body
    TOKENS["with_body"] += 1


# --- the D2 option A route class: 401 -> 415 -> 400 before FastAPI reads the body


def _declared_media_type(route: APIRoute) -> str | None:
    if route.body_field is None:
        return None
    return getattr(route.body_field.field_info, "media_type", None)


def _needs_session(route: APIRoute) -> bool:
    return any(dep.call is signed_in for dep in route.dependant.dependencies)


class _NotJSON(ValueError):
    pass


def _reject_constant(name: str) -> Any:
    raise _NotJSON(name)


class StrictJSONRequest(Request):
    """FastAPI's documented extension point (custom Request in a custom APIRoute): json() accepts exactly one UTF-8
    JSON value without NaN or Infinity. FastAPI's handler calls json() again and gets the cached value."""

    async def json(self) -> Any:
        if not hasattr(self, "_json"):
            raw = await self.body()
            try:
                self._json = json.loads(raw.decode(), parse_constant=_reject_constant)
            except ValueError as error:  # UnicodeDecodeError, JSONDecodeError, _NotJSON
                raise ProblemError(400, "bad-request", "request body is not valid JSON") from error
        return self._json


class OrderedRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()
        media_type = _declared_media_type(self)
        needs_session = _needs_session(self)

        async def ordered(request: Request) -> Response:
            request = StrictJSONRequest(request.scope, request.receive)
            if needs_session and not request.headers.get("x-user"):
                raise ProblemError(401, "unauthenticated", "no valid session", "session_invalid")
            if media_type is not None:
                message = email.message.Message()
                message["content-type"] = request.headers.get("content-type", "application/octet-stream")
                if message.get_content_type() != media_type:
                    raise ProblemError(415, "unsupported-media-type", f"accepted Content-Type: {media_type}")
                if await request.body():
                    await request.json()
            return await handler(request)

        return ordered


def problem_media_type_only(spec: dict[str, Any]) -> dict[str, Any]:
    """FastAPI writes an additional response's model under the route's success media type (application/json,
    fastapi/openapi/utils.py:491); a problem is application/problem+json. Moves the schema there."""
    for operations in spec["paths"].values():
        for operation in operations.values():
            for response in operation.get("responses", {}).values():
                content = response.get("content", {})
                if PROBLEM in content and "application/json" in content:
                    content[PROBLEM] = content.pop("application/json")
    return spec


def build(route_class: type[APIRoute] = APIRoute, *, fix_problem_media_type: bool = False) -> FastAPI:
    app = FastAPI(redirect_slashes=False, openapi_url="/api/openapi.json", docs_url=None, redoc_url=None)
    router = APIRouter(prefix="/api", route_class=route_class)
    problem_responses: dict[int | str, dict[str, Any]] = {
        status: {"model": Problem, "content": {PROBLEM: {}}, "description": slug}
        for status, slug in ((401, "unauthenticated"), (415, "unsupported-media-type"), (422, "validation"))
    }

    @router.post("/items", status_code=201, responses=problem_responses)
    async def create_item(principal: SignedIn, body: ItemCreate) -> Item:
        del principal
        return Item(id=uuid.uuid4(), title=body.title, description=body.description)

    @router.patch("/items/{id}", responses=problem_responses)
    async def patch_item(
        id: uuid.UUID, principal: SignedIn, body: Annotated[ItemPatch, Body(media_type=MERGE_PATCH)]
    ) -> Item:
        del principal
        title = body.title if body.title is not MISSING else "kept"
        description = body.description if body.description is not MISSING else None
        return Item(id=id, title=title, description=description)

    @router.post("/sessions", status_code=201, dependencies=[PUBLIC, Depends(rate_limit)], responses=problem_responses)
    async def create_session(body: SessionCreate) -> None:
        del body

    @router.post(
        "/sessions-b",
        status_code=201,
        dependencies=[PUBLIC, Depends(rate_limit_with_body)],
        responses=problem_responses,
    )
    async def create_session_b(body: SessionCreate) -> None:
        del body

    app.include_router(router)

    if fix_problem_media_type:
        native = app.openapi

        def openapi() -> dict[str, Any]:
            if app.openapi_schema is None:
                app.openapi_schema = problem_media_type_only(native())
            return app.openapi_schema

        app.openapi = openapi

    async def on_problem(_: Request, error: Exception) -> Response:
        assert isinstance(error, ProblemError)
        return problem(error.status, error.slug, error.detail, error.code)

    async def on_validation(_: Request, error: Exception) -> Response:
        assert isinstance(error, RequestValidationError)
        types = sorted({str(e["type"]) for e in error.errors()})
        return problem(422, "validation", ",".join(types))

    async def on_http(_: Request, error: Exception) -> Response:
        assert isinstance(error, HTTPException)
        return problem(error.status_code, "http", str(error.detail))

    app.add_exception_handler(ProblemError, on_problem)
    app.add_exception_handler(RequestValidationError, on_validation)
    app.add_exception_handler(HTTPException, on_http)

    return app
