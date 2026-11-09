"""The request body limit: every /api request's body is read here, whole, before routing, so a body over MAX_BODY_BYTES
is a 413 whatever the path, method or session (the first of the checks; app.http.route lists the rest).

Reading it here also keeps a client that goes away mid-body out of the 4xx range: ClientDisconnect leaves this
middleware and RecoverMiddleware answers 500, while FastAPI would turn it into a 400.
"""

from typing import TYPE_CHECKING

from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect

from app.http.problems import PAYLOAD_TOO_LARGE, problem_response

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Every request body is capped; the contract's largest body is a few hundred bytes.
MAX_BODY_BYTES = 1 << 20


class _BodyTooLargeError(Exception):
    pass


class BodyLimitMiddleware:
    """Reads the body of each request under /api/ and refuses it past MAX_BODY_BYTES, whether or not Content-Length
    announced it; the operation then receives the body already read."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        try:
            body = await _read_body(scope, receive)
        except _BodyTooLargeError:
            response = problem_response(PAYLOAD_TOO_LARGE, f"request body is over {MAX_BODY_BYTES} bytes")
            await response(scope, receive, send)
            return
        await self.app(scope, _replay(body, receive), send)


async def _read_body(scope: Scope, receive: Receive) -> bytes:
    """The whole body. A client that goes away mid-body raises ClientDisconnect: not the client's input, so not a
    4xx."""
    length = Headers(scope=scope).get("content-length")
    if length is not None and length.isdigit() and int(length) > MAX_BODY_BYTES:
        raise _BodyTooLargeError
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise ClientDisconnect
        chunk: bytes = message.get("body", b"")
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise _BodyTooLargeError
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
