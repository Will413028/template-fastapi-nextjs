"""Turns exceptions into problems: AppError into its problem, routing and binding failures into theirs, anything else
into a logged 500 whose details are never sent (they can hold SQL, hostnames or user data)."""

import logging
from typing import TYPE_CHECKING

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
            return _binding_problem(error)
        case HTTPException(status_code=status) if status in _STATUS_PROBLEMS:
            # Starlette's routing outside /api (a path no route has, a method it lacks; its Allow header is kept) and
            # what FastAPI's own helpers raise
            return problem_response(_STATUS_PROBLEMS[status], headers=error.headers)
        case _:
            raise error  # RecoverMiddleware logs it and answers 500


def _binding_problem(error: RequestValidationError) -> Response:
    """A route's own parsing failed on a request the contract accepted: its types are stricter than the contract's.
    That is still invalid input, so it gets the validation shape with a fixed detail (pydantic's messages echo the
    input), never FastAPI's {"detail": [...]}."""
    fields: list[FieldError] = []
    for item in error.errors():
        # ("body", "a", 0) points into the body, ("query", "limit") names a parameter: drop the location either way
        location: tuple[str | int, ...] = tuple(item.get("loc", ()))
        if item.get("type") == "missing":
            fields.append(FieldError(pointer(location[1:]), "required", "value is required"))
        else:
            fields.append(
                FieldError(pointer(location[1:]), "invalid_type", "value cannot be parsed as the declared type")
            )
    return validation_response(fields)
