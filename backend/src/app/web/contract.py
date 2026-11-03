"""The HTTP contract, backend/api/openapi.yaml, loaded with openapi-core.

openapi-core's own Starlette integration needs its [starlette] extra, which pins starlette below 1.1 and resolves to a
release with known vulnerabilities (docs/plans/2026-11-03-fastapi-template.md, step 2), so ContractRequest is the
adapter instead: it implements openapi-core's Request protocol from an ASGI request.
"""

import functools
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NoReturn, Protocol, Self, cast

from jsonschema import ValidationError, validators
from openapi_core import Config, OpenAPI
from openapi_core.datatypes import RequestParameters
from openapi_core.validation.schemas import oas30_write_schema_validators_factory
from openapi_core.validation.schemas.factories import SchemaValidatorsFactory
from starlette.datastructures import Headers, QueryParams
from starlette.requests import cookie_parser

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping
    from pathlib import Path

    from jsonschema.protocols import Validator
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


@functools.cache
def ecma_regex(pattern: str) -> re.Pattern[str]:
    """pattern compiled with ECMA-262's meaning, which OpenAPI's `pattern` uses, for what differs in Python's re:
    `$` outside a character class matches only at the end of the string (re also matches before a final newline,
    so "abc\\n" would pass ^[a-z]+$), and \\d, \\w and \\b are ASCII (re.ASCII). The Go version's RE2 agrees with
    ECMA on both."""
    out: list[str] = []
    in_class = False
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "\\":
            out.append(pattern[i : i + 2])
            i += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "$":
            char = r"\Z"
        out.append(char)
        i += 1
    return re.compile("".join(out), re.ASCII)


def _ecma_pattern(_: Validator, pattern: str, instance: object, _schema: object) -> Iterator[ValidationError]:
    # the message leaves the value out: it can be a password (app.web.validation writes the client's detail itself)
    if isinstance(instance, str) and not ecma_regex(pattern).search(instance):
        yield ValidationError(f"does not match {pattern!r}")


class _Extend(Protocol):
    def __call__(
        self, validator: type[Validator], validators: Mapping[str, Callable[..., object]]
    ) -> type[Validator]: ...


# jsonschema.validators.extend has no annotations; this is its documented signature, for pyright strict
_extend = cast("_Extend", vars(validators)["extend"])

# OAS 3.0's write validator (the one openapi-core picks for requests) with ECMA patterns. It also checks responses in
# the tests: the read and write validators differ only on readOnly and writeOnly, which the contract does not use.
_SCHEMA_VALIDATORS = SchemaValidatorsFactory(
    _extend(oas30_write_schema_validators_factory.schema_validator_cls, {"pattern": _ecma_pattern})
)


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
        config = Config(
            extra_media_type_deserializers=dict.fromkeys(JSON_MEDIA_TYPES, strict_json),
            schema_validators_factory=_SCHEMA_VALIDATORS,
        )
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
