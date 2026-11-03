"""The HTTP contract, backend/api/openapi.yaml, loaded with openapi-core.

openapi-core's own Starlette integration needs its [starlette] extra, which pins starlette below 1.1 and resolves to a
release with known vulnerabilities (docs/plans/2026-11-03-fastapi-template.md, step 2), so ContractRequest is the
adapter instead: it implements openapi-core's Request protocol from an ASGI request.
"""

import functools
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NoReturn, Protocol, Self, cast, override

from jsonschema import ValidationError, validators
from openapi_core import Config, OpenAPI
from openapi_core.datatypes import RequestParameters
from openapi_core.security.exceptions import SecurityProviderError
from openapi_core.security.factories import SecurityProviderFactory
from openapi_core.validation.schemas import oas30_write_schema_validators_factory
from openapi_core.validation.schemas.factories import SchemaValidatorsFactory
from starlette.datastructures import Headers, QueryParams
from starlette.requests import cookie_parser

from app.platform.principal import Principal, current_principal

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping
    from pathlib import Path

    from jsonschema.protocols import Validator
    from jsonschema_path import SchemaPath
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


# ECMA-262's \\s: WhiteSpace and LineTerminator (U+0009-000D, space, U+00A0, U+1680, U+2000-200A, U+2028, U+2029,
# U+202F, U+205F, U+3000, U+FEFF); re.ASCII would leave only the ASCII ones
_ECMA_SPACE = "\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
# ECMA's `.` matches anything but a line terminator; re's only excludes \\n
_ECMA_DOT = "[^\n\r\u2028\u2029]"


class UnsupportedPatternError(ValueError):
    """A contract pattern uses ECMA-262 syntax this translation does not carry over."""


@functools.cache
def ecma_regex(pattern: str) -> re.Pattern[str]:
    """pattern compiled with the meaning ECMA-262 gives it, which OpenAPI's `pattern` uses, where Python's re differs:

    - `$` outside a character class matches only at the end (re also matches before a final newline, so "abc\\n"
      would pass ^[a-z]+$);
    - \\d, \\w and \\b are ASCII (re.ASCII), while \\s and \\S keep ECMA's Unicode whitespace;
    - `.` does not match a line terminator.

    Anything re cannot compile (ECMA's named groups `(?<name>`, `\\k<name>`, a \\S inside a class) raises
    UnsupportedPatternError; Contract.load compiles every pattern, so that happens at startup, not on a request. The Go
    version's RE2 agrees with ECMA on what the contract uses."""
    out: list[str] = []
    in_class = False
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "\\":
            out.append(_escape(pattern, pattern[i : i + 2], in_class=in_class))
            i += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "$":
            char = r"\Z"
        elif char == ".":
            char = _ECMA_DOT
        out.append(char)
        i += 1
    try:
        return re.compile("".join(out), re.ASCII)
    except re.error as err:
        msg = f"pattern {pattern!r} is not supported: {err}"
        raise UnsupportedPatternError(msg) from err


def _escape(pattern: str, escape: str, *, in_class: bool) -> str:
    if escape == "\\s":
        return _ECMA_SPACE if in_class else f"[{_ECMA_SPACE}]"
    if escape == "\\S":
        if in_class:
            msg = f"pattern {pattern!r}: \\S inside a character class is not supported"
            raise UnsupportedPatternError(msg)
        return f"[^{_ECMA_SPACE}]"
    return escape


def _patterns(node: object) -> Iterator[str]:
    """Every `pattern` string in the contract document (schemas inline or under components)."""
    if isinstance(node, dict):
        for key, value in cast("dict[object, object]", node).items():
            if key == "pattern" and isinstance(value, str):
                yield value
            else:
                yield from _patterns(value)
    elif isinstance(node, list):
        for item in cast("list[object]", node):
            yield from _patterns(item)


def _ecma_pattern(_: Validator, pattern: str, instance: object, _schema: object) -> Iterator[ValidationError]:
    # the message leaves the value out: it can be a password (app.web.validation writes the client's detail itself)
    if isinstance(instance, str) and not ecma_regex(pattern).search(instance):
        yield ValidationError(f"does not match {pattern!r}")


class _Extend(Protocol):
    def __call__(
        self, validator: type[Validator], validators: Mapping[str, Callable[..., object]]
    ) -> type[Validator]: ...


# jsonschema.validators.extend has no annotations; this is its documented signature, for pyright strict
_extend: _Extend = cast("_Extend", vars(validators)["extend"])

# OAS 3.0's write validator (the one openapi-core picks for requests) with ECMA patterns. It also checks responses in
# the tests: the read and write validators differ only on readOnly and writeOnly, which the contract does not use.
_SCHEMA_VALIDATORS = SchemaValidatorsFactory(
    _extend(oas30_write_schema_validators_factory.schema_validator_cls, {"pattern": _ecma_pattern})
)


def _require_principal(_: RequestParameters) -> Principal:
    principal = current_principal()
    if principal is None:
        msg = "no signed-in session"
        raise SecurityProviderError(msg)
    return principal


class _SessionSecurity(SecurityProviderFactory):
    """The contract's security scheme is the session cookie; what satisfies it is a session the auth middleware
    (app.modules.auth.dependencies) found valid, not a cookie that is merely present: a stale or forged cookie is
    unauthenticated, whatever its name (`__Host-session`, or `session` with COOKIE_SECURE=false)."""

    @override
    def create(self, scheme: SchemaPath) -> Callable[[RequestParameters], Principal]:
        return _require_principal


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
            security_provider_factory=_SessionSecurity(),
        )
        try:
            openapi = OpenAPI.from_file_path(str(path), config=config)
            document: object = openapi.spec.read_value()
            for pattern in _patterns(document):
                ecma_regex(pattern)
            return cls(openapi, path.read_bytes())
        except Exception as err:  # a missing file, YAML or OpenAPI errors, a pattern: each raises its own type
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
