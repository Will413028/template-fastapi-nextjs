"""`app serve`: the HTTP server."""

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn

from app.main.factory import create_app
from app.platform.config import ConfigError, Settings
from app.platform.db import Database
from app.web.contract import Contract, ContractError

if TYPE_CHECKING:
    from collections.abc import Mapping

GRACEFUL_SHUTDOWN_SECONDS = 30

# Relative to the working directory: backend/ for `make run`, /app in the image (Dockerfile: WORKDIR /app, COPY api).
CONTRACT_PATH = Path("api/openapi.yaml")


def run(environ: Mapping[str, str] | None) -> int:
    try:
        settings = Settings.from_env(environ)
    except ConfigError as err:
        sys.stderr.write(f"serve: {err}\n")
        return 1
    try:
        contract = Contract.load(CONTRACT_PATH)
    except ContractError as err:
        sys.stderr.write(f"serve: {err}\n")
        return 1
    uvicorn.run(
        create_app(Database.open(settings.database), contract=contract, docs=settings.mode == "dev"),
        host=settings.http.host,
        port=settings.http.port,
        # Client addresses come only from the trusted origin header (added with the platform layer), never from
        # X-Forwarded-For that any client can send.
        proxy_headers=False,
        server_header=False,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )
    return 0
