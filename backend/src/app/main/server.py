"""`app serve`: the HTTP server."""

import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn

from app.main.factory import Deps, Modules, create_app
from app.main.logs import configure_logging
from app.main.routes import RouteContractError
from app.modules.auth.dependencies import Auth, SessionCookie
from app.modules.auth.service import AuthService
from app.modules.items.service import ItemsService
from app.platform.config import ConfigError, Settings
from app.platform.db import Database
from app.web.contract import Contract, ContractError
from app.web.origin import OriginPolicy
from app.web.ratelimit import IPLimiter

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
    auth = AuthService(database.engine, session_absolute=settings.session_absolute, session_idle=settings.session_idle)
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
                    auth=Auth(auth, SessionCookie(name=settings.session_cookie_name, secure=settings.cookie_secure)),
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
        # Client addresses come only from X-Real-IP on a request with the origin secret (app.web.origin), never from
        # X-Forwarded-For that any client can send.
        proxy_headers=False,
        # The access log is app.web.access_log's, and every logger writes through configure_logging's handler.
        access_log=False,
        log_config=None,
        server_header=False,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )
    return 0
