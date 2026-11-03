"""One log line per request once it ends (Go version: accessLog in internal/httpapi/middleware.go)."""

import logging
import time
from typing import TYPE_CHECKING

from app.web.origin import current_client, is_health_path

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("app.access")


class AccessLogMiddleware:
    """Logs method, path, status, duration_ms and client_ip. The query string is left out: it can hold cursors and
    search terms. Health checks log at debug level: the container probe calls them every few seconds.

    Only what RecoverMiddleware lets through escapes the app: an exception after the response started, or a
    BaseException such as a cancellation. That request was cut off: it is logged as a warning with the status sent
    before (0 for nothing) and the exception goes on to the server."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status = 0

        async def recording_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        def fields() -> dict[str, object]:
            client = current_client()
            address = client.address if client else None
            return {
                "method": scope["method"],
                "path": scope["path"],
                "status": status,
                "duration_ms": round((time.perf_counter() - start) * 1000, 3),
                "client_ip": str(address) if address else None,
            }

        try:
            await self.app(scope, receive, recording_send)
        except BaseException:
            logger.warning("request aborted", extra=fields())
            raise
        level = logging.DEBUG if is_health_path(scope["path"]) else logging.INFO
        logger.log(level, "request", extra=fields())
