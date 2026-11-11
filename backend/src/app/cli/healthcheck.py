"""`app healthcheck`: the container healthcheck. It asks the local server whether it is ready (GET /health/ready).

Standard library only, so it starts in a fraction of a second even on a loaded host: importing the web framework here
once took it to 6.7 s on a loaded host. A contract in .importlinter keeps it that way.
"""

import http
import http.client
import sys
from typing import TYPE_CHECKING

from app.core.config import ConfigError, HttpSettings

if TYPE_CHECKING:
    from collections.abc import Mapping

HEALTHCHECK_TIMEOUT_SECONDS = 2


def run(environ: Mapping[str, str] | None) -> int:
    try:
        settings = HttpSettings.from_env(environ)
    except ConfigError as err:
        sys.stderr.write(f"healthcheck: {err}\n")
        return 1
    conn = http.client.HTTPConnection(settings.probe_host, settings.port, timeout=HEALTHCHECK_TIMEOUT_SECONDS)
    try:
        conn.request("GET", "/health/ready")
        status = conn.getresponse().status
    except OSError as err:
        sys.stderr.write(f"healthcheck: {err}\n")
        return 1
    finally:
        conn.close()
    if status != http.HTTPStatus.OK:
        sys.stderr.write(f"healthcheck: /health/ready returned {status}\n")
        return 1
    return 0
