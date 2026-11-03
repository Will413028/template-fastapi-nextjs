"""`app serve`: the HTTP server."""

import sys
from typing import TYPE_CHECKING

import uvicorn

from app.main.factory import create_app
from app.platform.config import ConfigError, Settings
from app.platform.db import Database

if TYPE_CHECKING:
    from collections.abc import Mapping

GRACEFUL_SHUTDOWN_SECONDS = 30


def run(environ: Mapping[str, str] | None) -> int:
    try:
        settings = Settings.from_env(environ)
    except ConfigError as err:
        sys.stderr.write(f"serve: {err}\n")
        return 1
    uvicorn.run(
        create_app(Database.open(settings.database)),
        host=settings.http.host,
        port=settings.http.port,
        # Client addresses come only from the trusted origin header (added with the platform layer), never from
        # X-Forwarded-For that any client can send.
        proxy_headers=False,
        server_header=False,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )
    return 0
