"""`app openapi`: writes the contract, the OpenAPI document generated from the routes (app.main.build_openapi), to
stdout. `make gen` saves it as api/openapi.json and `make gen-check` fails when that file is stale. It reads no
settings and builds none of the app's dependencies.
"""

import json
import sys
from typing import TYPE_CHECKING

from app.main import build_openapi

if TYPE_CHECKING:
    from collections.abc import Mapping


def render() -> str:
    """The document as committed: two-space indents, keys in FastAPI's order, a final newline. Non-ASCII is escaped,
    so the bytes do not depend on the locale's stdout encoding."""
    return json.dumps(build_openapi(), indent=2) + "\n"


def run(environ: Mapping[str, str] | None) -> int:
    del environ
    sys.stdout.write(render())
    return 0
