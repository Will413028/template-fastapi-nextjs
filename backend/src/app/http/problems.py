"""RFC 9457 problem responses, with the same `type` and `title` values as the Go version (internal/httpapi/problem.go).

Clients choose messages by type and code (docs/rules/api-design.md), so changing a slug is a breaking change. Every
problem is built from the Problem and ValidationProblem models the contract declares, and every type and code sent
must be listed in their x-extensible-enum (tests/apitest.py checks each one tests see).
"""

import math
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Annotated

from fastapi.responses import JSONResponse
from pydantic import MISSING, BaseModel, Field

from app.core.errors import AppError, Kind
from app.http.request_id import current_request_id

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

PROBLEM_CONTENT_TYPE = "application/problem+json"


@dataclass(frozen=True, slots=True)
class ProblemType:
    status: HTTPStatus
    slug: str
    title: str

    @property
    def uri(self) -> str:
        return f"/problems/{self.slug}"


BAD_REQUEST = ProblemType(HTTPStatus.BAD_REQUEST, "bad-request", "Bad request")
UNAUTHENTICATED = ProblemType(HTTPStatus.UNAUTHORIZED, "unauthenticated", "Unauthenticated")
FORBIDDEN = ProblemType(HTTPStatus.FORBIDDEN, "forbidden", "Forbidden")
NOT_FOUND = ProblemType(HTTPStatus.NOT_FOUND, "not-found", "Not found")
METHOD_NOT_ALLOWED = ProblemType(HTTPStatus.METHOD_NOT_ALLOWED, "method-not-allowed", "Method not allowed")
CONFLICT = ProblemType(HTTPStatus.CONFLICT, "conflict", "Conflict")
PAYLOAD_TOO_LARGE = ProblemType(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "payload-too-large", "Payload too large")
UNSUPPORTED_MEDIA_TYPE = ProblemType(
    HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "unsupported-media-type", "Unsupported media type"
)
VALIDATION = ProblemType(HTTPStatus.UNPROCESSABLE_CONTENT, "validation", "Validation failed")
RATE_LIMITED = ProblemType(HTTPStatus.TOO_MANY_REQUESTS, "rate-limited", "Too many requests")
INTERNAL = ProblemType(HTTPStatus.INTERNAL_SERVER_ERROR, "internal", "Internal error")
UNAVAILABLE = ProblemType(HTTPStatus.SERVICE_UNAVAILABLE, "unavailable", "Service unavailable")

PROBLEM_TYPES = (
    BAD_REQUEST,
    UNAUTHENTICATED,
    FORBIDDEN,
    NOT_FOUND,
    METHOD_NOT_ALLOWED,
    CONFLICT,
    PAYLOAD_TOO_LARGE,
    UNSUPPORTED_MEDIA_TYPE,
    VALIDATION,
    RATE_LIMITED,
    INTERNAL,
    UNAVAILABLE,
)

KIND_PROBLEMS = {
    Kind.NOT_FOUND: NOT_FOUND,
    Kind.CONFLICT: CONFLICT,
    Kind.UNAUTHENTICATED: UNAUTHENTICATED,
    Kind.FORBIDDEN: FORBIDDEN,
    Kind.VALIDATION: VALIDATION,
    Kind.RATE_LIMITED: RATE_LIMITED,
}

# Every problem code the API sends. The lists grow (x-extensible-enum, ADR api-and-auth-baseline D3): adding a value
# is not a breaking change, renaming or removing one is. tests/apitest.py fails a test whose response sends a value
# the generated contract does not list.
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

# Every FieldError code: the failed request rules (app.http.errors.VALIDATION_CODES and the rules it applies before
# them) and the business rules.
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


# One invalid input. detail never repeats the input value: it can end up in a client's error report, and the value can
# be a password. (A comment, not a docstring: the docstring would be the schema's description in the contract.)
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


def pointer(tokens: Iterable[str | int]) -> str:
    """A JSON Pointer (RFC 6901) from its reference tokens; the empty pointer is the whole document."""
    return "".join("/" + str(t).replace("~", "~0").replace("/", "~1") for t in tokens)


def problem_response(
    problem: ProblemType, detail: str = "", code: str = "", headers: Mapping[str, str] | None = None
) -> JSONResponse:
    """A problem of type problem; detail and code are left out when empty."""
    body = Problem(
        type=problem.uri,
        title=problem.title,
        status=int(problem.status),
        detail=detail or MISSING,
        code=code or MISSING,
        request_id=_request_id(),
    )
    return _response(problem, body, headers)


def validation_response(fields: Iterable[FieldError]) -> JSONResponse:
    """A validation problem listing each invalid input."""
    body = ValidationProblem(
        type=VALIDATION.uri,
        title=VALIDATION.title,
        status=int(VALIDATION.status),
        request_id=_request_id(),
        errors=list(fields),
    )
    return _response(VALIDATION, body)


def app_error_response(error: AppError) -> JSONResponse:
    if error.kind is Kind.VALIDATION:
        return validation_response([FieldError(pointer=error.pointer, code=error.code, detail=error.detail)])
    headers = None
    # RFC 9110 delay-seconds is a non-negative integer; a wait that already elapsed sends none, as the Go version
    if error.kind is Kind.RATE_LIMITED and error.retry_after is not None and error.retry_after.total_seconds() > 0:
        headers = {"Retry-After": str(math.ceil(error.retry_after.total_seconds()))}
    return problem_response(KIND_PROBLEMS[error.kind], error.detail, error.code, headers)


def _request_id() -> str | MISSING:
    # the same id as the X-Request-ID header, which RequestIdMiddleware sets around everything that sends a problem
    request_id = current_request_id()
    return MISSING if request_id is None else request_id


def _response(problem: ProblemType, body: Problem, headers: Mapping[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        body.model_dump(mode="json"), status_code=problem.status, headers=headers, media_type=PROBLEM_CONTENT_TYPE
    )
