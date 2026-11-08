"""The contract's shared pieces: the problem bodies every error response carries, the problem responses and headers
operations declare, and the pagination parameters every list operation takes.

The contract is generated from the routes and these models (app.main.build_openapi, ADR fastapi-backend-stack). A
model's docstring and its fields' descriptions are the contract's text: write them for the API's clients.
"""

from http import HTTPStatus
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Query
from pydantic import MISSING, BaseModel, Field

from app.core.pagination import MAX_LIMIT
from app.http.problems import PROBLEM_CONTENT_TYPE, PROBLEM_TYPES

if TYPE_CHECKING:
    from collections.abc import Mapping

# Every problem code the API sends. The lists grow (x-extensible-enum, ADR api-and-auth-baseline D3): adding a value
# is not a breaking change, renaming or removing one is. Until openapi.yaml is removed
# (docs/plans/2026-11-08-fastapi-code-first.md), tests/apitest.py fails a test whose response sends a value
# openapi.yaml does not list, and the plan's contract_delta.py fails when these lists and the yaml's differ.
PROBLEM_CODES = (
    "account_taken",
    "invalid_credentials",
    "login_throttled",
    "last_admin",
    "reauthentication_required",
    "session_invalid",
    "insufficient_role",
    "cross_origin_request",
)

# Every FieldError code: the failed contract rules (app.http.validation) and the business rules.
FIELD_ERROR_CODES = (
    "required",
    "too_short",
    "too_long",
    "pattern",
    "enum",
    "format",
    "out_of_range",
    "invalid_type",
    "null_not_allowed",
    "additional_property",
    "invalid",
    "password_too_common",
)

_GROWS = "The list grows (x-extensible-enum), so clients translate the values they know and fall back for the rest."
_NOT_PARSED = "Human-readable explanation for developers; clients must not parse it."


class Problem(BaseModel):
    """RFC 9457 problem details."""

    type: Annotated[
        str,
        Field(
            description=f"Fixed per kind of problem. {_GROWS}",
            examples=["/problems/not-found"],
            json_schema_extra={"format": "uri-reference", "x-extensible-enum": [p.uri for p in PROBLEM_TYPES]},
        ),
    ]
    title: Annotated[str, Field(description="Fixed per `type`.")]
    status: Annotated[int, Field(description="The HTTP status code.")]
    detail: Annotated[str | MISSING, Field(description=_NOT_PARSED)] = MISSING
    code: Annotated[
        str | MISSING,
        Field(
            description=f"Stable machine-readable code refining `type`. {_GROWS}",
            json_schema_extra={"x-extensible-enum": list(PROBLEM_CODES)},
        ),
    ] = MISSING
    request_id: Annotated[
        str | MISSING, Field(description="The request's `X-Request-ID`, for matching a report with the server logs.")
    ] = MISSING


class FieldError(BaseModel):
    pointer: Annotated[
        str,
        Field(
            description="JSON Pointer (RFC 6901) to the invalid value in the request body, or `/<name>` for a query or "
            "path parameter. Empty when the whole body is missing.",
            examples=["/title"],
        ),
    ]
    code: Annotated[
        str,
        Field(
            description="Stable machine-readable code: a failed contract rule, or a business rule such as "
            f"`password_too_common`. {_GROWS}",
            examples=["too_short"],
            json_schema_extra={"x-extensible-enum": list(FIELD_ERROR_CODES)},
        ),
    ]
    detail: Annotated[str, Field(description=_NOT_PARSED)]


class ValidationProblem(Problem):
    """A problem with type `/problems/validation`, listing each invalid input."""

    errors: list[FieldError]


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
    """The problem responses of an operation, for its route's `responses=`. Every operation with a parameter or a body
    declares 422; otherwise FastAPI adds its own HTTPValidationError schema."""
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
