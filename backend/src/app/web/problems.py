"""RFC 9457 problem responses, with the same `type` and `title` values as the Go version (internal/httpapi/problem.go).

Clients choose messages by type and code (docs/rules/api-design.md), so changing a slug is a breaking change. Every
type and code sent must be listed in the contract's x-extensible-enum (tests/apitest.py checks each one tests see).
"""

import math
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse

from app.platform.errors import AppError, Kind
from app.web.request_id import current_request_id

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


@dataclass(frozen=True, slots=True)
class FieldError:
    """One invalid input: a JSON Pointer to it (empty for the whole body), a code from the contract's FieldError enum,
    and a detail for developers that never repeats the input value."""

    pointer: str
    code: str
    detail: str


def pointer(tokens: Iterable[str | int]) -> str:
    """A JSON Pointer (RFC 6901) from its reference tokens; the empty pointer is the whole document."""
    return "".join("/" + str(t).replace("~", "~0").replace("/", "~1") for t in tokens)


def problem_response(
    problem: ProblemType, detail: str = "", code: str = "", headers: Mapping[str, str] | None = None
) -> JSONResponse:
    """A problem of type problem; detail and code are left out when empty."""
    body = _base(problem)
    if detail:
        body["detail"] = detail
    if code:
        body["code"] = code
    return _response(problem, body, headers)


def validation_response(fields: Iterable[FieldError]) -> JSONResponse:
    """A validation problem listing each invalid input."""
    body = _base(VALIDATION)
    body["errors"] = [{"pointer": f.pointer, "code": f.code, "detail": f.detail} for f in fields]
    return _response(VALIDATION, body)


def app_error_response(error: AppError) -> JSONResponse:
    if error.kind is Kind.VALIDATION:
        return validation_response([FieldError(error.pointer, error.code, error.detail)])
    headers = None
    # RFC 9110 delay-seconds is a non-negative integer; a wait that already elapsed sends none, as the Go version
    if error.kind is Kind.RATE_LIMITED and error.retry_after is not None and error.retry_after.total_seconds() > 0:
        headers = {"Retry-After": str(math.ceil(error.retry_after.total_seconds()))}
    return problem_response(KIND_PROBLEMS[error.kind], error.detail, error.code, headers)


def _base(problem: ProblemType) -> dict[str, Any]:
    body: dict[str, Any] = {"type": problem.uri, "title": problem.title, "status": int(problem.status)}
    # the same id as the X-Request-ID header, which RequestIdMiddleware sets around everything that sends a problem
    request_id = current_request_id()
    if request_id is not None:
        body["request_id"] = request_id
    return body


def _response(problem: ProblemType, body: dict[str, Any], headers: Mapping[str, str] | None = None) -> JSONResponse:
    return JSONResponse(body, status_code=problem.status, headers=headers, media_type=PROBLEM_CONTENT_TYPE)
