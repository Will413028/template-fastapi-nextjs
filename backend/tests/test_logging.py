"""Structured logs and the access log (Go version: internal/platform/logx and accessLog in
internal/httpapi/middleware.go; the cases match TestPanicLogAndAccessLogCarryRequestID and
TestAccessLogAbortBeforeWriting in middleware_test.go)."""

import asyncio
import io
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from apitest import (
    EMPTY_BODY,
    LOOPBACK,
    PEER,
    BoomError,
    bare_app,
    call_asgi,
    checked_client,
    stream_then_fail,
    stub_app,
)
from app.main.logs import configure_logging
from app.web.origin import OriginPolicy

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from starlette.types import Message

    from app.platform.config import Mode

SECRET = "s" * 43
PROD = OriginPolicy(secrets=(SECRET,), required=True)

type Capture = Callable[..., io.StringIO]


@pytest.fixture
def capture() -> Iterator[Capture]:
    """configure_logging writing to a buffer; the root logger is restored afterwards."""
    root = logging.getLogger()
    saved = (root.level, list(root.handlers))
    # the test client's own request lines are not the app's
    client_log = logging.getLogger("httpx2")
    client_level = client_log.level
    client_log.setLevel(logging.WARNING)

    def configure(mode: Mode = "prod", level: int = logging.INFO) -> io.StringIO:
        stream = io.StringIO()
        configure_logging(mode, stream, level=level)
        return stream

    yield configure
    root.setLevel(saved[0])
    root.handlers[:] = saved[1]
    client_log.setLevel(client_level)


def lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def find(entries: list[dict[str, Any]], msg: str) -> dict[str, Any]:
    matches = [e for e in entries if e["msg"] == msg]
    assert len(matches) == 1, f"{msg!r} logged {len(matches)} times in {entries}"
    return matches[0]


def test_panic_log_and_access_log_carry_request_id(capture: Capture) -> None:
    stream = capture()
    with checked_client(stub_app(origin=PROD)) as client:
        response = client.get(
            "/api/fail?cursor=secret", headers={"x-origin-secret": SECRET, "x-real-ip": "198.51.100.9"}
        )
    request_id = response.headers["x-request-id"]
    entries = lines(stream)

    error = find(entries, "unhandled error")
    assert error["request_id"] == request_id
    assert error["level"] == "ERROR"
    assert BoomError.__name__ in error["exc_info"]
    access = find(entries, "request")
    want = {
        "request_id": request_id,
        "level": "INFO",
        "method": "GET",
        "path": "/api/fail",
        "status": 500,
        "client_ip": "198.51.100.9",
    }
    assert {k: access.get(k) for k in want} == want
    assert isinstance(access["duration_ms"], float)
    # the query string can hold cursors and search terms
    assert "cursor" not in stream.getvalue()


def test_access_log_client_ip_is_the_connection_without_the_secret(capture: Capture) -> None:
    stream = capture()
    with checked_client(stub_app()) as client:
        client.get("/api/widgets", headers={"x-real-ip": "198.51.100.9"})
    assert find(lines(stream), "request")["client_ip"] == PEER[0]


@pytest.mark.parametrize("secret", [None, "wrong-" + SECRET], ids=["no secret", "wrong secret"])
def test_a_refused_request_is_logged_with_the_connection_address(capture: Capture, secret: str | None) -> None:
    # a forged X-Real-IP is ignored whether the secret is missing or wrong
    stream = capture()
    headers = {"x-real-ip": "198.51.100.9"}
    if secret:
        headers["x-origin-secret"] = secret
    with checked_client(stub_app(origin=PROD)) as client:
        client.get("/api/widgets", headers=headers)
    access = find(lines(stream), "request")
    assert (access["status"], access["client_ip"]) == (403, PEER[0])


def test_health_checks_log_at_debug(capture: Capture) -> None:
    stream = capture(level=logging.DEBUG)
    with checked_client(stub_app(), peer=LOOPBACK) as client:
        client.get("/health")
    assert find(lines(stream), "request")["level"] == "DEBUG"

    stream = capture()
    with checked_client(stub_app(), peer=LOOPBACK) as client:
        client.get("/health")
    assert stream.getvalue() == "", "the container probe every few seconds stays out of the default log"


class AbortError(BaseException):
    """What escapes RecoverMiddleware: not an Exception, such as the cancellation of a disconnected client."""


async def abort() -> None:
    raise AbortError


def test_access_log_abort_before_writing(capture: Capture) -> None:
    stream = capture()
    app = bare_app()
    app.get("/api/widgets/{widget_id}")(abort)
    with pytest.raises(AbortError):
        asyncio.run(call_asgi(app, {"method": "GET", "path": f"/api/widgets/{uuid.uuid7()}"}, [EMPTY_BODY]))
    aborted = find(lines(stream), "request aborted")
    assert (aborted["level"], aborted["status"]) == ("WARNING", 0), "nothing was sent"


def test_access_log_abort_after_the_response_started(capture: Capture) -> None:
    stream = capture()
    app = bare_app()
    stream_then_fail(app, "/api/widgets")
    with pytest.raises(BoomError):
        asyncio.run(call_asgi(app, {"method": "GET", "path": "/api/widgets"}, [EMPTY_BODY]))
    aborted = find(lines(stream), "request aborted")
    assert (aborted["level"], aborted["status"]) == ("WARNING", 200)


def test_the_servers_error_line_carries_the_request_id(capture: Capture) -> None:
    stream = capture()
    app = bare_app()
    stream_then_fail(app, "/api/widgets")
    sent: list[Message] = []

    async def serve() -> None:
        # as uvicorn does: the app, then the server's own error line in the same task
        try:
            await call_asgi(app, {"method": "GET", "path": "/api/widgets"}, [EMPTY_BODY], sent=sent)
        except BoomError:
            logging.getLogger("uvicorn.error").exception("Exception in ASGI application")

    asyncio.run(serve())
    request_id = dict(sent[0]["headers"])[b"x-request-id"].decode()
    assert find(lines(stream), "Exception in ASGI application")["request_id"] == request_id


def test_dev_logs_are_text_with_the_request_id(capture: Capture) -> None:
    stream = capture("dev")
    with checked_client(stub_app()) as client:
        request_id = client.get("/api/widgets").headers["x-request-id"]
    line = stream.getvalue().strip()
    assert not line.startswith("{")
    assert "request" in line
    assert f"request_id={request_id}" in line
    assert "status=200" in line


def test_text_log_values_cannot_forge_a_line(capture: Capture) -> None:
    stream = capture("dev")
    with checked_client(stub_app()) as client:
        client.get('/api/x%0A2026-01-01 INFO app.access request status=200 x="')
    output = stream.getvalue()
    assert output.count("\n") == 1, output
    assert 'path="/api/x\\n2026-01-01 INFO app.access request status=200 x=\\""' in output


def test_a_message_with_request_data_stays_on_one_line(capture: Capture) -> None:
    stream = capture("dev")
    logging.getLogger("app.test").warning("bad header %s", "x\r\n2026-01-01 ERROR app.access forged")
    assert stream.getvalue().count("\n") == 1, stream.getvalue()


def test_extra_fields_cannot_replace_the_core_keys(capture: Capture) -> None:
    stream = capture()
    logging.getLogger("app.test").warning("real", extra={"level": "DEBUG", "logger": "forged"})
    entry = find(lines(stream), "real")
    assert (entry["level"], entry["logger"]) == ("WARNING", "app.test")


def test_a_log_line_outside_a_request_has_no_request_id(capture: Capture) -> None:
    stream = capture()
    logging.getLogger("app.test").info("starting", extra={"port": 8000, "color_message": "\x1b[1mstarting\x1b[0m"})
    entry = find(lines(stream), "starting")
    assert "request_id" not in entry
    assert entry["port"] == 8000
    assert entry["logger"] == "app.test"
    assert "color_message" not in entry  # uvicorn's copy of msg with terminal colour codes
