"""Checks every /api request against the contract before an operation sees it, so length, format and enum rules live
only in backend/api/openapi.yaml (docs/rules/api-design.md).

Failures become problems, as in the Go version (internal/httpapi/validate.go): an undefined route 404 (405 for a path
without that method), a body over MAX_BODY_BYTES 413, a missing session where the operation declares security 401, a
Content-Type the operation does not accept 415, a body that is not valid JSON 400, and anything else 422 listing each
invalid input. Only requests are validated in production; tests validate responses (tests/apitest.py).
"""

import json
from typing import TYPE_CHECKING, Any, cast

from jsonschema.exceptions import ValidationError as SchemaError
from openapi_core.casting.schemas.exceptions import CastError
from openapi_core.deserializing.media_types.exceptions import MediaTypeDeserializeError
from openapi_core.deserializing.styles.exceptions import EmptyQueryParameterValue
from openapi_core.templating.media_types.exceptions import MediaTypeNotFound
from openapi_core.templating.paths.exceptions import OperationNotFound, PathError
from openapi_core.validation.request.exceptions import (
    InvalidRequestBody,
    MissingRequiredParameter,
    MissingRequiredRequestBody,
    ParameterValidationError,
    RequestBodyValidationError,
    SecurityValidationError,
)
from openapi_core.validation.schemas.exceptions import InvalidSchemaValue
from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect

from app.web.contract import ContractRequest
from app.web.problems import (
    BAD_REQUEST,
    METHOD_NOT_ALLOWED,
    NOT_FOUND,
    PAYLOAD_TOO_LARGE,
    UNAUTHENTICATED,
    UNSUPPORTED_MEDIA_TYPE,
    FieldError,
    pointer,
    problem_response,
    validation_response,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from starlette.responses import Response
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

    from app.web.contract import Contract

# Every request body is capped; the contract's largest body is a few hundred bytes.
MAX_BODY_BYTES = 1 << 20

# A failed JSON Schema keyword to the stable code clients translate. Keywords not listed become "invalid"; a null where
# the schema is not nullable is "null_not_allowed".
SCHEMA_CODES = {
    "required": "required",
    "minLength": "too_short",
    "minItems": "too_short",
    "maxLength": "too_long",
    "maxItems": "too_long",
    "pattern": "pattern",
    "enum": "enum",
    "format": "format",
    "minimum": "out_of_range",
    "maximum": "out_of_range",
    "exclusiveMinimum": "out_of_range",
    "exclusiveMaximum": "out_of_range",
    "type": "invalid_type",
    "nullable": "null_not_allowed",
    "additionalProperties": "additional_property",
}


class UnexpectedValidationError(Exception):
    """openapi-core reported an error this module does not know: a 500, so a new kind of error is noticed."""


class BodyTooLargeError(Exception):
    pass


class ContractValidation:
    """Validates requests under /api/ against contract; skip names paths served outside the contract (MODE=dev's
    docs)."""

    def __init__(self, app: ASGIApp, contract: Contract, skip: frozenset[str] = frozenset()) -> None:
        self.app = app
        self.contract = contract
        self.skip = skip

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/") or scope["path"] in self.skip:
            await self.app(scope, receive, send)
            return
        try:
            body = await _read_body(scope, receive)
        except BodyTooLargeError:
            response = problem_response(PAYLOAD_TOO_LARGE, f"request body is over {MAX_BODY_BYTES} bytes")
            await response(scope, receive, send)
            return
        request = ContractRequest.from_scope(scope, body)
        rejection = _reject(request, self.contract.openapi.iter_request_errors(request))
        if rejection is not None:
            await rejection(scope, receive, send)
            return
        await self.app(scope, _replay(body, receive), send)


async def _read_body(scope: Scope, receive: Receive) -> bytes:
    """The whole body, refused past MAX_BODY_BYTES whether or not Content-Length announced it. A client that goes away
    mid-body raises ClientDisconnect: not the client's input, so not a 4xx."""
    length = Headers(scope=scope).get("content-length")
    if length is not None and length.isdigit() and int(length) > MAX_BODY_BYTES:
        raise BodyTooLargeError
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise ClientDisconnect
        chunk: bytes = message.get("body", b"")
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise BodyTooLargeError
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


def _replay(body: bytes, receive: Receive) -> Receive:
    """receive for the operation: the body already read, then the connection's own messages (disconnect)."""
    pending = True

    async def replay() -> Message:
        nonlocal pending
        if pending:
            pending = False
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


def _reject(request: ContractRequest, found: Iterator[Exception]) -> Response | None:
    errors = list(found)
    if not errors:
        return None
    # A route or security error comes alone: openapi-core stops there, so an anonymous caller learns nothing about the
    # request it would have to send. A media type or JSON error wins over the other errors listed with it.
    for error in errors:
        response = _whole_request_problem(error)
        if response is not None:
            return response
    fields: dict[tuple[str, str], FieldError] = {}
    for error in errors:
        for field in _fields(request, error):
            # openapi-core reports a null where null is not allowed twice (nullable and type)
            fields.setdefault((field.pointer, field.code), field)
    return validation_response(fields.values())


def _whole_request_problem(error: Exception) -> Response | None:
    if isinstance(error, OperationNotFound):
        return problem_response(METHOD_NOT_ALLOWED)
    if isinstance(error, PathError):  # PathNotFound, ServerNotFound (outside the contract's /api)
        return problem_response(NOT_FOUND)
    if isinstance(error, SecurityValidationError):
        return problem_response(UNAUTHENTICATED, "this operation needs a signed-in session")
    if isinstance(error, RequestBodyValidationError):
        cause = error.__cause__
        if isinstance(cause, MediaTypeNotFound):
            # the accepted types, never the request's own header
            return problem_response(
                UNSUPPORTED_MEDIA_TYPE, "accepted Content-Type: " + ", ".join(sorted(cause.availableMimetypes))
            )
        if isinstance(cause, MediaTypeDeserializeError):
            return problem_response(BAD_REQUEST, "request body is not valid JSON")
    return None


def _fields(request: ContractRequest, error: Exception) -> list[FieldError]:
    if isinstance(error, MissingRequiredRequestBody):
        return [FieldError("", "required", "value is required")]
    if isinstance(error, InvalidRequestBody) and isinstance(error.__cause__, InvalidSchemaValue):
        return _schema_fields("", error.__cause__)
    if isinstance(error, ParameterValidationError):
        return _parameter_fields(request, error)
    msg = f"unhandled request validation error {type(error).__name__}"
    raise UnexpectedValidationError(msg) from error


def _parameter_fields(request: ContractRequest, error: ParameterValidationError) -> list[FieldError]:
    at = pointer([error.name])
    cause = error.__cause__
    if isinstance(error, MissingRequiredParameter):
        return [FieldError(at, "required", "value is required")]
    if isinstance(cause, InvalidSchemaValue):
        return _schema_fields(at, cause)
    # An empty value (?limit=) is present, so not "required"; the parameter may still be optional.
    if isinstance(cause, EmptyQueryParameterValue) or (
        error.location == "query" and request.query.get(error.name) == ""
    ):
        return [FieldError(at, "invalid", "value must not be empty")]
    if isinstance(cause, CastError):
        return [FieldError(at, "invalid_type", "value cannot be parsed as the declared type")]
    msg = f"unhandled parameter error {type(cause).__name__}"
    raise UnexpectedValidationError(msg) from error


def _schema_fields(base: str, invalid: InvalidSchemaValue) -> list[FieldError]:
    fields: list[FieldError] = []
    for error in invalid.schema_errors:
        if not isinstance(error, SchemaError):
            msg = f"unhandled schema error {type(error).__name__}"
            raise UnexpectedValidationError(msg) from invalid
        fields.extend(_schema_error_fields(base + pointer(error.absolute_path), error))
    return fields


def _schema_error_fields(at: str, error: SchemaError) -> Iterable[FieldError]:
    keyword = str(error.validator)
    instance: Any = error.instance
    rule: Any = error.validator_value
    if keyword == "required":
        # one error per missing property, all on the object: point at each property instead
        required: list[str] = rule
        return [
            FieldError(at + pointer([name]), "required", "value is required")
            for name in required
            if name not in instance
        ]
    if keyword == "additionalProperties":
        declared: dict[str, Any] = error.schema.get("properties", {}) if isinstance(error.schema, dict) else {}
        return [
            FieldError(at + pointer([name]), "additional_property", "property is not allowed")
            for name in sorted(instance)
            if name not in declared
        ]
    if instance is None and keyword in {"type", "nullable"}:
        return [FieldError(at, "null_not_allowed", "value must not be null")]
    return [FieldError(at, SCHEMA_CODES.get(keyword, "invalid"), _detail(keyword, rule))]


# What each rule asks for, without the input value: a detail can end up in a client's error report, and the value can
# be a password.
_DETAILS = {
    "minLength": "must be at least {} characters long",
    "maxLength": "must be at most {} characters long",
    "minItems": "must have at least {} items",
    "maxItems": "must have at most {} items",
    "minimum": "must be at least {}",
    "maximum": "must be at most {}",
    "pattern": "must match {}",
    "format": "must be a valid {}",
    "type": "must be of type {}",
}


def _detail(keyword: str, rule: object) -> str:
    if keyword == "enum" and isinstance(rule, list):
        return "must be one of " + ", ".join(json.dumps(v) for v in cast("list[object]", rule))
    template = _DETAILS.get(keyword)
    return template.format(rule) if template else "invalid value"
