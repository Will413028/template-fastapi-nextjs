"""Request ids: every request gets a new UUIDv7, sent back in X-Request-ID and in every problem body.

An X-Request-ID from outside is never believed: no trusted proxy sets one (D4 in
docs/adr/2026-11-05-vercel-rewrite-and-cloudflare-tunnel.md), and a client-chosen id would let it forge or collide
log lines.
"""

import contextvars
import uuid
from typing import TYPE_CHECKING

from starlette.datastructures import MutableHeaders

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


def current_request_id() -> str | None:
    """The id of the request being served; None outside one."""
    return _request_id.get()


class RequestIdMiddleware:
    """Outermost, so that every response, problems included, carries the id (app.main.factory)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid.uuid7())

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        token = _request_id.set(request_id)
        await self.app(scope, receive, send_with_id)
        # Reset only when the app returns. An exception goes on to the server, which logs it in the same task (uvicorn
        # runs each request in its own), so that line keeps the id; the task and its context end with the request.
        _request_id.reset(token)
