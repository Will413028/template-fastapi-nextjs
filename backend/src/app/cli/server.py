"""`app serve`: the HTTP server."""

import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn

from app.cli.logs import configure_logging
from app.core.config import ConfigError, Settings
from app.core.db import Database
from app.core.hibp import HIBPClient
from app.http.contract import Contract, ContractError
from app.http.origin import OriginPolicy
from app.http.ratelimit import IPLimiter
from app.main import Deps, Modules, RouteContractError, create_app
from app.modules.auth.dependencies import Auth, SessionCookie
from app.modules.auth.service import AuthService, SessionLimits
from app.modules.items.service import ItemsService

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
    configure_logging(settings.mode)
    database = Database.open(settings.database)
    auth = AuthService(
        database.engine,
        sessions=SessionLimits(absolute=settings.session_absolute, idle=settings.session_idle),
        breach=HIBPClient() if settings.password_hibp_check else None,
    )
    try:
        app = create_app(
            Deps(
                database=database,
                contract=contract,
                origin=OriginPolicy(secrets=settings.origin_secrets, required=settings.mode == "prod"),
                app_origin=settings.app_origin,
                docs=settings.mode == "dev",
                limiter=IPLimiter(settings.auth_rate_limit_per_min, time.monotonic),
                modules=Modules(
                    auth=Auth(
                        auth,
                        SessionCookie(
                            name=settings.session_cookie_name,
                            device_name=settings.device_cookie_name,
                            secure=settings.cookie_secure,
                        ),
                    ),
                    items=ItemsService(database.engine),
                ),
            )
        )
    except RouteContractError as err:
        # nothing has connected or started a thread yet: the engine connects and the hash pool starts on first use
        sys.stderr.write(f"serve: routes and contract disagree: {err}\n")
        return 1
    uvicorn.run(
        app,
        host=settings.http.host,
        port=settings.http.port,
        # Client addresses come only from X-Real-IP on a request with the origin secret (app.http.origin), never from
        # X-Forwarded-For that any client can send.
        proxy_headers=False,
        # The access log is app.http.access_log's, and every logger writes through configure_logging's handler.
        access_log=False,
        log_config=None,
        server_header=False,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )
    return 0
