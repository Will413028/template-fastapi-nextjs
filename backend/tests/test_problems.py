"""Problem responses (Go version: internal/httpapi/problem_test.go and problem_contract_test.go)."""

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from apitest import (
    EMPTY_BODY,
    LOOPBACK,
    BoomError,
    assert_problem,
    bare_app,
    call_asgi,
    checked_client,
    contract_values,
    stream_then_fail,
    stub_app,
    values_outside_contract,
)
from app.platform.errors import AppError, Kind
from app.web.problems import KIND_PROBLEMS, PROBLEM_TYPES
from app.web.validation import SCHEMA_CODES

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI
    from starlette.types import Message


def raising(error: Exception) -> FastAPI:
    """An app whose GET /api/widgets raises error."""
    app = bare_app()

    @app.get("/api/widgets")
    async def raise_it() -> None:
        raise error

    return app


@pytest.mark.parametrize(
    ("error", "status", "problem_type"),
    [
        (AppError(Kind.NOT_FOUND, "", "no such item"), 404, "/problems/not-found"),
        (AppError(Kind.CONFLICT, "account_taken", "account exists"), 409, "/problems/conflict"),
        (AppError(Kind.UNAUTHENTICATED, "session_invalid"), 401, "/problems/unauthenticated"),
        (AppError(Kind.FORBIDDEN, "insufficient_role"), 403, "/problems/forbidden"),
        (AppError(Kind.RATE_LIMITED, "login_throttled"), 429, "/problems/rate-limited"),
    ],
)
def test_app_error_becomes_its_problem(error: AppError, status: int, problem_type: str) -> None:
    with checked_client(raising(error)) as client:
        problem = assert_problem(client.get("/api/widgets"), status, problem_type)
    assert problem.get("code") == (error.code or None)
    assert problem.get("detail") == (error.detail or None)


def test_app_error_validation_lists_the_field() -> None:
    error = AppError.invalid("/password", "password_too_common", "on the common-password list")
    with checked_client(raising(error)) as client:
        problem = assert_problem(client.get("/api/widgets"), 422, "/problems/validation")
    assert problem["errors"] == [
        {"pointer": "/password", "code": "password_too_common", "detail": "on the common-password list"}
    ]
    assert "code" not in problem


def test_rate_limited_sends_retry_after_rounded_up() -> None:
    error = AppError(Kind.RATE_LIMITED, "login_throttled", retry_after=timedelta(milliseconds=1500))
    with checked_client(raising(error)) as client:
        response = client.get("/api/widgets")
    assert_problem(response, 429, "/problems/rate-limited")
    assert response.headers["retry-after"] == "2"


def test_unhandled_error_is_logged_not_sent(caplog: pytest.LogCaptureFixture) -> None:
    internal = 'relation "users" does not exist at db.internal:5432 (canary-7f3a)'
    with checked_client(raising(RuntimeError(internal))) as client:
        response = client.get("/api/widgets")
    problem = assert_problem(response, 500, "/problems/internal")
    assert "canary-7f3a" not in response.text
    assert "detail" not in problem
    assert "canary-7f3a" in caplog.text


def test_unhandled_error_log_has_the_stack(caplog: pytest.LogCaptureFixture) -> None:
    with checked_client(stub_app()) as client:
        response = client.get("/api/fail")
    assert_problem(response, 500, "/problems/internal")
    assert "boom" not in response.text
    [record] = [r for r in caplog.records if r.exc_info]
    assert record.levelno == logging.ERROR
    assert "boom" in caplog.text
    assert "Traceback" in caplog.text


def test_failure_after_the_response_started_aborts_it() -> None:
    # Appending a problem would turn a partial 200 into a 200 with corrupt JSON: the response is cut off instead.
    app = bare_app()
    stream_then_fail(app, "/api/widgets")
    sent: list[Message] = []
    with pytest.raises(BoomError):
        asyncio.run(call_asgi(app, {"method": "GET", "path": "/api/widgets"}, [EMPTY_BODY], sent=sent))
    assert sent[0]["status"] == 200
    assert not any(b"problems" in m.get("body", b"") for m in sent)


def test_unknown_path_outside_the_api_is_a_problem() -> None:
    with checked_client(stub_app(), peer=LOOPBACK) as client:
        assert_problem(client.get("/nothing-here"), 404, "/problems/not-found")
        assert_problem(client.post("/health"), 405, "/problems/method-not-allowed")


def test_problem_values_are_in_the_contract() -> None:
    values = contract_values()
    assert {p.uri for p in PROBLEM_TYPES} <= values["type"]
    assert {p.uri for p in KIND_PROBLEMS.values()} <= values["type"]
    assert set(SCHEMA_CODES.values()) | {"invalid", "invalid_type", "required"} <= values["field"]


def test_values_outside_the_contract_are_reported() -> None:
    # the check itself
    body = b'{"type":"/problems/teapot","code":"brewing","errors":[{"pointer":"/a","code":"too_hot","detail":""}]}'
    assert values_outside_contract(body) == ["type /problems/teapot", "code brewing", "field code too_hot"]
    assert values_outside_contract(b'{"type":"/problems/validation","errors":[{"code":"too_short"}]}') == []


@pytest.mark.parametrize("seconds", [0, -3])
def test_rate_limited_without_a_positive_wait_has_no_retry_after(seconds: int) -> None:
    error = AppError(Kind.RATE_LIMITED, "login_throttled", retry_after=timedelta(seconds=seconds))
    with checked_client(raising(error)) as client:
        response = client.get("/api/widgets")
    assert_problem(response, 429, "/problems/rate-limited")
    assert "retry-after" not in response.headers


def test_other_http_errors_keep_their_status_and_headers() -> None:
    # FastAPI and Starlette raise HTTPException themselves (security helpers, request parsing); a status with a
    # problem type keeps it instead of becoming a 500
    with checked_client(raising(HTTPException(401, headers={"WWW-Authenticate": "Cookie"}))) as client:
        response = client.get("/api/widgets")
    assert_problem(response, 401, "/problems/unauthenticated")
    assert response.headers["www-authenticate"] == "Cookie"
    with checked_client(stub_app(), peer=LOOPBACK) as client:
        assert client.post("/health").headers["allow"] == "GET"


async def _stream_then_app_error() -> AsyncIterator[bytes]:
    yield b'{"items":['
    raise AppError(Kind.CONFLICT, "account_taken")


def test_app_error_after_the_response_started_aborts_it() -> None:
    app = bare_app()

    @app.get("/api/widgets")
    async def stream() -> StreamingResponse:
        return StreamingResponse(_stream_then_app_error(), media_type="application/json")

    sent: list[Message] = []
    with pytest.raises(RuntimeError, match="response already started"):
        asyncio.run(call_asgi(app, {"method": "GET", "path": "/api/widgets"}, [EMPTY_BODY], sent=sent))
    assert sent[0]["status"] == 200
    assert not any(b"problems" in m.get("body", b"") for m in sent)
