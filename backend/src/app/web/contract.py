"""The HTTP contract, backend/api/openapi.yaml, loaded with openapi-core.

openapi-core's own Starlette integration needs its [starlette] extra, which pins starlette below 1.1 and resolves to a
release with known vulnerabilities (docs/plans/2026-11-03-fastapi-template.md, step 2), so ContractRequest is the
adapter instead: it implements openapi-core's Request protocol from an ASGI request.
"""

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NoReturn, Self

from openapi_core import Config, OpenAPI
from openapi_core.datatypes import RequestParameters
from starlette.datastructures import Headers, QueryParams
from starlette.requests import cookie_parser

if TYPE_CHECKING:
    from pathlib import Path

    from starlette.types import Scope

# openapi-core reads the contract's servers entry (/api) against this origin; the Host header plays no part, because
# the backend does not know which name the browser used (D6).
_ORIGIN = "http://backend"

# The JSON media types the contract uses: request bodies, and problems for the tests that validate responses.
# openapi-core reads the last two as raw bytes unless told otherwise, and its JSON reader accepts NaN and Infinity,
# which JSON (RFC 8259) does not have.
JSON_MEDIA_TYPES = ("application/json", "application/merge-patch+json", "application/problem+json")


def _reject_constant(name: str) -> NoReturn:
    msg = f"{name} is not JSON"
    raise ValueError(msg)


def strict_json(value: bytes, **_parameters: str) -> object:
    """Exactly one UTF-8 JSON value. openapi-core reports the ValueError this raises otherwise (UnicodeDecodeError and
    JSONDecodeError are ValueErrors too) as a MediaTypeDeserializeError."""
    return json.loads(value.decode(), parse_constant=_reject_constant)


class ContractError(Exception):
    """The contract file cannot be read or is not valid OpenAPI."""


@dataclass(frozen=True, slots=True)
class Contract:
    openapi: OpenAPI
    # the file as written, for MODE=dev's /api/openapi.yaml
    raw: bytes

    @classmethod
    def load(cls, path: Path) -> Self:
        """Reads and validates the contract; a contract that is not valid OpenAPI fails here, at startup."""
        config = Config(extra_media_type_deserializers=dict.fromkeys(JSON_MEDIA_TYPES, strict_json))
        try:
            return cls(OpenAPI.from_file_path(str(path), config=config), path.read_bytes())
        except Exception as err:  # a missing file, YAML or OpenAPI errors: each library raises its own type
            msg = f"load the contract {path}: {err}"
            raise ContractError(msg) from err


@dataclass(slots=True)
class ContractRequest:
    """openapi-core's Request protocol for one request: the method, path, query, headers and the body already read."""

    method: str
    path: str
    query: QueryParams
    headers: Headers
    body: bytes | None
    host_url: str = _ORIGIN
    parameters: RequestParameters = field(init=False)

    def __post_init__(self) -> None:
        self.method = self.method.lower()
        cookies = cookie_parser(self.headers.get("cookie", ""))
        self.parameters = RequestParameters(query=self.query, header=self.headers, cookie=cookies)

    @classmethod
    def from_scope(cls, scope: Scope, body: bytes) -> Self:
        return cls(
            method=scope["method"],
            path=scope["path"],
            query=QueryParams(scope["query_string"]),
            headers=Headers(scope=scope),
            body=body,
        )

    @property
    def content_type(self) -> str:
        # RFC 9110: a body without Content-Type may be treated as application/octet-stream
        return self.headers.get("content-type", "application/octet-stream").lower()
