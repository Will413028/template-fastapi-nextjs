"""Turns exceptions into problems: AppError into its problem, routing failures and invalid input (422) into theirs,
anything else into a logged 500 whose details are never sent (they can hold SQL, hostnames or user data)."""

import logging
from typing import TYPE_CHECKING, Any

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from app.core.errors import AppError
from app.http.problems import (
    INTERNAL,
    PROBLEM_TYPES,
    VALIDATION,
    FieldError,
    app_error_response,
    pointer,
    problem_response,
    validation_response,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import FastAPI, Request
    from starlette.responses import Response
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

_STATUS_PROBLEMS = {int(p.status): p for p in PROBLEM_TYPES if p not in {INTERNAL, VALIDATION}}


class RecoverMiddleware:
    """An exception that escapes the app becomes a logged 500 problem. It sits inside RequestIdMiddleware and
    SecurityHeadersMiddleware, so that problem carries the request id and the headers too, which Starlette's own
    ServerErrorMiddleware (outside every middleware) cannot do.

    Once the response has started, appending a problem would turn a partial 200 into a 200 with corrupt JSON: the
    exception is re-raised and the server cuts the response off.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            if started:
                raise
            logger.exception("unhandled error", extra={"method": scope["method"], "path": scope["path"]})
            await problem_response(INTERNAL)(scope, receive, send)


def install_exception_handlers(app: FastAPI) -> None:
    for error_class in (AppError, HTTPException, RequestValidationError):
        app.add_exception_handler(error_class, _handle)


async def _handle(_: Request, error: Exception) -> Response:
    match error:
        case AppError():
            return app_error_response(error)
        case RequestValidationError():
            return _validation_problem(error)
        case HTTPException(status_code=status) if status in _STATUS_PROBLEMS:
            # Starlette's routing (a path no route has, a method it lacks; its Allow header is kept) and what FastAPI's
            # own helpers raise
            return problem_response(_STATUS_PROBLEMS[status], headers=error.headers)
        case _:
            raise error  # RecoverMiddleware logs it and answers 500


def _validation_problem(error: RequestValidationError) -> Response:
    """Each invalid parameter and body value, never FastAPI's {"detail": [...]}: parameters first, then the body
    (FastAPI checks them in that order), one error per pointer and code."""
    fields: dict[tuple[str, str], FieldError] = {}
    for item in error.errors():
        field = field_error(item)
        fields.setdefault((field.pointer, field.code), field)
    return validation_response(fields.values())


# A Pydantic error type to the stable code clients translate (FieldError.code). field_error decides a null where null
# is not allowed and an empty query value before it, and a type not listed becomes "invalid" and is logged, so a new
# kind of error is noticed.
VALIDATION_CODES = {
    "missing": "required",
    "extra_forbidden": "additional_property",
    "string_too_short": "too_short",
    "too_short": "too_short",
    "string_too_long": "too_long",
    "too_long": "too_long",
    "string_pattern_mismatch": "pattern",
    "literal_error": "enum",
    "enum": "enum",
    "uuid_parsing": "format",
    "uuid_version": "format",
    "datetime_parsing": "format",
    "datetime_from_date_parsing": "format",
    "timezone_aware": "format",
    "greater_than": "out_of_range",
    "greater_than_equal": "out_of_range",
    "less_than": "out_of_range",
    "less_than_equal": "out_of_range",
    "multiple_of": "invalid",
    "string_type": "invalid_type",
    "int_type": "invalid_type",
    "int_parsing": "invalid_type",
    "int_from_float": "invalid_type",
    "float_type": "invalid_type",
    "float_parsing": "invalid_type",
    "bool_type": "invalid_type",
    "bool_parsing": "invalid_type",
    "dict_type": "invalid_type",
    "list_type": "invalid_type",
    "model_type": "invalid_type",
    "model_attributes_type": "invalid_type",
    "uuid_type": "invalid_type",
    "datetime_type": "invalid_type",
}

# What each rule asks for, from the error's context and never its input or message (both can carry the value).
_DETAILS = {
    "missing": "value is required",
    "extra_forbidden": "property is not allowed",
    "string_too_short": "must be at least {min_length} characters long",
    "too_short": "must have at least {min_length} items",
    "string_too_long": "must be at most {max_length} characters long",
    "too_long": "must have at most {max_length} items",
    "string_pattern_mismatch": "must match {pattern}",
    "literal_error": "must be one of {expected}",
    "enum": "must be one of {expected}",
    "uuid_parsing": "must be a valid uuid",
    "uuid_version": "must be a valid uuid",
    "datetime_parsing": "must be a valid date-time",
    "datetime_from_date_parsing": "must be a valid date-time",
    "timezone_aware": "must be a date-time with a time zone",
    "greater_than": "must be greater than {gt}",
    "greater_than_equal": "must be at least {ge}",
    "less_than": "must be less than {lt}",
    "less_than_equal": "must be at most {le}",
    "multiple_of": "must be a multiple of {multiple_of}",
    "string_type": "must be of type string",
    "int_type": "must be of type integer",
    "int_parsing": "must be of type integer",
    "int_from_float": "must be of type integer",
    "float_type": "must be of type number",
    "float_parsing": "must be of type number",
    "bool_type": "must be of type boolean",
    "bool_parsing": "must be of type boolean",
    "dict_type": "must be of type object",
    "list_type": "must be of type array",
    "model_type": "must be of type object",
    "model_attributes_type": "must be of type object",
    "uuid_type": "must be of type string",
    "datetime_type": "must be of type string",
}


def field_error(item: Mapping[str, Any]) -> FieldError:
    """One Pydantic error as the contract's FieldError. Its loc starts with where the value was ("body", "query",
    "path"); the rest points into it: ("body", "parts", "a/b") is /parts/a~1b, ("query", "limit") is /limit."""
    location: tuple[str | int, ...] = tuple(item.get("loc", ()))
    at = pointer(location[1:])
    kind = str(item.get("type", ""))
    value = item.get("input")
    # a null for a declared property that is not nullable; an undeclared one is additional_property whatever its value
    if kind not in {"missing", "extra_forbidden"} and value is None:
        return FieldError(pointer=at, code="null_not_allowed", detail="value must not be null")
    # ?limit= is present, so not "required"; the parameter may still be optional
    if location[:1] == ("query",) and value == "":
        return FieldError(pointer=at, code="invalid", detail="value must not be empty")
    code = VALIDATION_CODES.get(kind)
    if code is None:
        logger.warning("validation error type without a code", extra={"error_type": kind})
        return FieldError(pointer=at, code="invalid", detail="invalid value")
    context: dict[str, Any] = dict(item.get("ctx") or {})
    return FieldError(pointer=at, code=code, detail=_DETAILS[kind].format_map(context))
