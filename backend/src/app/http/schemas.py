"""The contract's shared pieces: the problem responses and headers operations declare, and the pagination parameters
every list operation takes. The problem bodies themselves are app.http.problems' models.

The contract is generated from the routes and the modules' models (app.main.build_openapi, ADR fastapi-backend-stack).
A model's docstring and its fields' descriptions are the contract's text: write them for the API's clients.
"""

import re
import uuid
from http import HTTPStatus
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Query
from pydantic import BeforeValidator, ConfigDict
from pydantic_core import PydanticCustomError

from app.core.pagination import MAX_LIMIT
from app.http.problems import PROBLEM_CONTENT_TYPE, Problem, ValidationProblem

if TYPE_CHECKING:
    from collections.abc import Mapping

# Every request body model's config: a property the model does not declare is a 422 `additional_property`, and a value
# of the wrong JSON type a 422 `invalid_type` instead of being converted ("true" for a boolean, "3" for an integer), as
# the contract's JSON Schema says. Strict mode would refuse a UUID or a date-time sent as a string; give such a field
# Field(strict=False).
REQUEST_CONFIG = ConfigDict(extra="forbid", strict=True)

# Each problem status an operation can declare, with its description. app.main moves the schema to
# application/problem+json (FastAPI writes it under the operation's success media type).
_PROBLEMS: dict[int, tuple[str, type[Problem]]] = {
    400: ("The request is malformed, for example a body that is not valid JSON.", Problem),
    401: ("No valid session.", Problem),
    403: ("Authenticated but not allowed, or a cross-origin state-changing request.", Problem),
    404: ("The resource does not exist or is not visible to the caller.", Problem),
    409: ("The request conflicts with the current state, for example a taken account.", Problem),
    413: ("The request body is over the size limit.", Problem),
    415: ("The request body's Content-Type is not one the operation accepts.", Problem),
    422: ("The request does not match the contract or a business rule.", ValidationProblem),
    429: ("Too many requests.", Problem),
    500: ("An unexpected error; details are only in the server log.", Problem),
}

# The only request media type a PATCH accepts (RFC 7396): `body: Annotated[Patch, Body(media_type=MERGE_PATCH)]`.
MERGE_PATCH = "application/merge-patch+json"


def _header(description: str, schema: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"description": description, "schema": dict(schema or {"type": "string"})}


_RETRY_AFTER = _header("Seconds to wait before retrying.", {"type": "integer", "minimum": 0})

LOCATION = {"Location": _header("The URL of the created resource.")}
SET_SESSION_COOKIE = {"Set-Cookie": _header("The new session cookie.")}
CLEAR_SESSION_COOKIE = {
    "Set-Cookie": _header(
        "Expires the session cookie. On deleteSession, sent only when the revoked session is the current one."
    )
}


def problems(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """The problem responses of an operation: its route's `responses=` lists the ones its own code and the
    middleware send (404, 409, a cross-origin 403, 500), and app.http.route.ApiRoute adds the ones it checks."""
    responses: dict[int | str, dict[str, Any]] = {}
    for status in statuses:
        description, model = _PROBLEMS[status]
        response: dict[str, Any] = {"description": description, "model": model, "content": {PROBLEM_CONTENT_TYPE: {}}}
        if status == HTTPStatus.TOO_MANY_REQUESTS:
            response["headers"] = {"Retry-After": _RETRY_AFTER}
        responses[status] = response
    return responses


def headers(status: int, *groups: Mapping[str, Any]) -> dict[int | str, dict[str, Any]]:
    """The headers of an operation's success response, for its route's `responses=` (with problems: `|`)."""
    merged: dict[str, Any] = {}
    for group in groups:
        merged |= group
    return {status: {"headers": merged}}


# list operations default to app.core.pagination.DEFAULT_LIMIT: `limit: Limit = DEFAULT_LIMIT`
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT, description="Page size.")]
Cursor = Annotated[
    str | None, Query(description="Opaque cursor from a previous page's `next_cursor`; omit for the first page.")
]

# A UUID as the contract's `format: uuid` reads it: the hyphenated form. Pydantic alone also takes it without hyphens,
# in braces or as a urn:uuid: URN; those stay a 422 `format` (uuid_parsing in app.http.errors) as they always were.
_HYPHENATED_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _hyphenated(value: object) -> object:
    if isinstance(value, str) and not _HYPHENATED_UUID.fullmatch(value):
        kind, msg = "uuid_parsing", "Input should be a hyphenated UUID"
        raise PydanticCustomError(kind, msg)
    return value


# a path parameter that names a resource: `id: Id`
Id = Annotated[uuid.UUID, BeforeValidator(_hyphenated)]
