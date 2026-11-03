"""`app serve`: the HTTP server."""

from typing import TYPE_CHECKING

import uvicorn

from app.main.factory import create_app

if TYPE_CHECKING:
    from app.platform.config import Settings

GRACEFUL_SHUTDOWN_SECONDS = 30


def run(settings: Settings) -> int:
    uvicorn.run(
        create_app(),
        host=settings.host,
        port=settings.port,
        # Client addresses come only from the trusted origin header (added with the platform layer), never from
        # X-Forwarded-For that any client can send.
        proxy_headers=False,
        server_header=False,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )
    return 0
