"""Settings read once at startup. The only module that reads environment variables (ruff TID251 bans it elsewhere).

A missing required value or a malformed one never falls back to a default: every problem is collected and reported
together, so the process fails at startup. Variables and defaults match `.env-example`.

Any variable KEY can instead be given as KEY_FILE, the path of a file holding the value (compose secrets mount them
under /run/secrets); one trailing newline is dropped. Setting both is an error. Passwords never sit in a connection
URL: the URL names the user and the password comes from its own variable.

Standard library only: `app healthcheck` loads this module every few seconds (.importlinter, light-healthcheck).
"""

import ipaddress
import os
import pathlib
import re
import urllib.parse
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Literal, Self

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

type Mode = Literal["dev", "prod"]

# An empty host binds every interface, as HTTP_ADDR=":8000" does for the Go version this template mirrors.
DEFAULT_ADDR = ":8000"
MAX_PORT = 65535
DEFAULT_DB_MAX_CONNS = 20
DEFAULT_DB_STATEMENT_TIMEOUT = 5.0
DEFAULT_AUTH_RATE_LIMIT_PER_MIN = 5
DEFAULT_SESSION_ABSOLUTE = timedelta(hours=168)
DEFAULT_SESSION_IDLE = timedelta(hours=24)
# The current origin secret and, while a rotation is under way, the previous one.
MAX_ORIGIN_SECRETS = 2
# scripts/init_env.py writes 43 characters (32 random bytes in base64url); a short one could be guessed.
MIN_ORIGIN_SECRET_LENGTH = 32
_PRINTABLE_ASCII = re.compile(r"[!-~]+")
# The query parameters a connection URL may carry; anything else would be silently ignored by the driver.
_URL_PARAMS = frozenset({"sslmode"})
_SSLMODES = frozenset({"disable", "allow", "prefer", "require", "verify-ca", "verify-full"})
# Go's time.ParseDuration syntax, as the Go version reads these variables: 5s, 1m30s, 168h, 250ms, .5s, 0.
_DURATION = re.compile(r"(\d+\.?\d*|\.\d+)(ns|us|µs|μs|ms|s|m|h)")
_UNIT_SECONDS = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 1e-3, "us": 1e-6, "µs": 1e-6, "μs": 1e-6, "ns": 1e-9}
_INTEGER = re.compile(r"[0-9]+")
# strconv.ParseBool's spellings, as the Go version reads COOKIE_SECURE
_BOOLEANS = dict.fromkeys(("1", "t", "T", "TRUE", "true", "True"), True) | dict.fromkeys(
    ("0", "f", "F", "FALSE", "false", "False"), False
)
_DEFAULT_PORTS = {"http": 80, "https": 443}


class ConfigError(ValueError):
    """A setting is missing or malformed; the process must not start."""


@dataclass(frozen=True, slots=True)
class HttpSettings:
    host: str
    port: int

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Self:
        env = _Env(os.environ if environ is None else environ)
        http = env.http_addr()
        env.raise_problems()
        return cls(*http)

    @property
    def probe_host(self) -> str:
        """The address a local client dials: this machine when the server listens on every interface."""
        if not self.host:
            return "127.0.0.1"
        try:
            unspecified = ipaddress.ip_address(self.host).is_unspecified
        except ValueError:
            return self.host
        return "127.0.0.1" if unspecified else self.host


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """A PostgreSQL connection. `url` names the user but has no password."""

    url: str
    password: str
    max_conns: int = DEFAULT_DB_MAX_CONNS
    statement_timeout: float | None = DEFAULT_DB_STATEMENT_TIMEOUT
    lock_timeout: float = 3.0

    @property
    def parts(self) -> urllib.parse.SplitResult:
        return urllib.parse.urlsplit(self.url)

    @property
    def user(self) -> str:
        return urllib.parse.unquote(self.parts.username or "")

    @property
    def database_name(self) -> str:
        return urllib.parse.unquote(self.parts.path.lstrip("/"))

    @property
    def sslmode(self) -> str:
        """libpq's sslmode; libpq's default when the URL has none."""
        return urllib.parse.parse_qs(self.parts.query).get("sslmode", ["prefer"])[0]


# The session cookie's name with COOKIE_SECURE=true (the default), as the contract documents it (app.http.access).
SECURE_SESSION_COOKIE = "__Host-session"


@dataclass(frozen=True, slots=True)
class Settings:
    """What `app serve` needs."""

    mode: Mode
    http: HttpSettings
    database: DatabaseSettings
    # The site's origin as browsers send it in Origin (scheme://host[:port], lowercase, no default port): the CSRF
    # check compares against it because the Host the backend receives is not the name the browser used
    # (ADR vercel-rewrite-and-cloudflare-tunnel).
    app_origin: str
    # X-Origin-Secret values that mark a request as coming through the site
    # (ADR vercel-rewrite-and-cloudflare-tunnel D3); empty only with MODE=dev.
    origin_secrets: tuple[str, ...] = ()
    auth_rate_limit_per_min: int = DEFAULT_AUTH_RATE_LIMIT_PER_MIN
    # A session ends session_absolute after sign-in, or session_idle after its last use (ADR api-and-auth-baseline D4).
    session_absolute: timedelta = DEFAULT_SESSION_ABSOLUTE
    session_idle: timedelta = DEFAULT_SESSION_IDLE
    # false only for local development over http
    cookie_secure: bool = True
    # check new passwords against Have I Been Pwned (app.core.hibp)
    password_hibp_check: bool = False

    @property
    def session_cookie_name(self) -> str:
        """The `__Host-` prefix requires Secure, so a cookie that is not Secure drops it."""
        return SECURE_SESSION_COOKIE if self.cookie_secure else "session"

    @property
    def device_cookie_name(self) -> str:
        """The trusted-device cookie, named by the same rule as the session cookie."""
        return "__Host-device" if self.cookie_secure else "device"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Self:
        env = _Env(os.environ if environ is None else environ)
        mode = env.mode()
        http = env.http_addr()
        url = env.database_url("DATABASE_URL")
        password = env.required("DATABASE_PASSWORD")
        max_conns = env.parse("DB_MAX_CONNS", DEFAULT_DB_MAX_CONNS, _parse_int)
        timeout = env.parse("DB_STATEMENT_TIMEOUT", DEFAULT_DB_STATEMENT_TIMEOUT, _parse_duration)
        if max_conns < 1:
            env.fail("DB_MAX_CONNS must be positive")
        if timeout <= 0:
            env.fail("DB_STATEMENT_TIMEOUT must be positive")
        origin_secrets = env.origin_secrets(mode)
        per_min = env.parse("AUTH_RATE_LIMIT_PER_MIN", DEFAULT_AUTH_RATE_LIMIT_PER_MIN, _parse_int)
        if per_min < 1:
            env.fail("AUTH_RATE_LIMIT_PER_MIN must be positive")
        app_origin = env.app_origin()
        absolute = timedelta(
            seconds=env.parse("SESSION_ABSOLUTE", DEFAULT_SESSION_ABSOLUTE.total_seconds(), _parse_duration)
        )
        idle = timedelta(seconds=env.parse("SESSION_IDLE", DEFAULT_SESSION_IDLE.total_seconds(), _parse_duration))
        if idle <= timedelta(0) or absolute <= timedelta(0):
            env.fail("SESSION_IDLE and SESSION_ABSOLUTE must be positive")
        elif idle > absolute:
            env.fail(f"SESSION_IDLE ({idle}) must not exceed SESSION_ABSOLUTE ({absolute})")
        cookie_secure = env.parse("COOKIE_SECURE", default=True, parse=_parse_bool)
        if mode == "prod" and not cookie_secure:
            # production is always behind HTTPS (Vercel); a cookie without Secure or the __Host- prefix there is a
            # misconfiguration (.env-example and docs/deploy-vps.md: COOKIE_SECURE MUST be true in production)
            env.fail("COOKIE_SECURE must be true when MODE=prod")
        hibp = env.parse("PASSWORD_HIBP_CHECK", default=False, parse=_parse_bool)
        env.raise_problems()
        return cls(
            mode=mode,
            http=HttpSettings(*http),
            database=DatabaseSettings(url=url, password=password, max_conns=max_conns, statement_timeout=timeout),
            app_origin=app_origin,
            origin_secrets=origin_secrets,
            auth_rate_limit_per_min=per_min,
            session_absolute=absolute,
            session_idle=idle,
            cookie_secure=cookie_secure,
            password_hibp_check=hibp,
        )


@dataclass(frozen=True, slots=True)
class MigrateSettings:
    """What `app migrate` needs: it connects as the owner role and grants DML to the app role serve connects as."""

    database: DatabaseSettings
    app_role: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Self:
        env = _Env(os.environ if environ is None else environ)
        url = env.database_url("MIGRATE_DATABASE_URL")
        password = env.required("MIGRATE_DATABASE_PASSWORD")
        app_role = env.required("DB_APP_USER")
        env.raise_problems()
        # A DDL statement queued behind a long transaction would block everything queued behind it; a timed-out
        # migrate is simply re-run. Migrations themselves get no statement timeout.
        database = DatabaseSettings(url=url, password=password, max_conns=1, statement_timeout=None, lock_timeout=5.0)
        return cls(database=database, app_role=app_role)


@dataclass(frozen=True, slots=True)
class SeedSettings:
    """What `app seed-admin` and `app recover-admin` need: the database as serve connects to it, the admin account,
    and whether new passwords are checked against Have I Been Pwned as serve does. ADMIN_PASSWORD is normally
    ADMIN_PASSWORD_FILE=/dev/stdin (the root Makefile's seed and recover-admin targets), so the password never
    sits on a command line or in an environment variable."""

    database: DatabaseSettings
    admin_account: str
    admin_password: str
    password_hibp_check: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Self:
        env = _Env(os.environ if environ is None else environ)
        url = env.database_url("DATABASE_URL")
        password = env.required("DATABASE_PASSWORD")
        account = env.required("ADMIN_ACCOUNT")
        admin_password = env.required("ADMIN_PASSWORD")
        hibp = env.parse("PASSWORD_HIBP_CHECK", default=False, parse=_parse_bool)
        env.raise_problems()
        return cls(
            database=DatabaseSettings(url=url, password=password, max_conns=1),
            admin_account=account,
            admin_password=admin_password,
            password_hibp_check=hibp,
        )


class _Env:
    def __init__(self, environ: Mapping[str, str]) -> None:
        self.environ = environ
        self.problems: list[str] = []

    def fail(self, problem: str) -> None:
        self.problems.append(problem)

    def raise_problems(self) -> None:
        if self.problems:
            raise ConfigError("; ".join(self.problems))

    def get(self, key: str) -> str | None:
        """KEY, or the contents of the file KEY_FILE names. An empty value counts as unset."""
        value = self.environ.get(key, "")
        path = self.environ.get(f"{key}_FILE", "")
        if not path:
            return value or None
        if value:
            self.fail(f"set only one of {key} and {key}_FILE")
            return None
        try:
            # the path is deployment configuration, not input from a client
            text = pathlib.Path(path).read_text(encoding="utf-8")
        except OSError as err:
            self.fail(f"read {key}_FILE: {err.strerror}: {path}")
            return None
        return text.removesuffix("\n").removesuffix("\r") or None

    def required(self, key: str) -> str:
        value = self.get(key)
        if value is None:
            self.fail(f"missing required environment variable {key} (or {key}_FILE)")
            return ""
        return value

    def parse[T](self, key: str, default: T, parse: Callable[[str], T]) -> T:
        raw = self.get(key)
        if raw is None:
            return default
        try:
            return parse(raw)
        except ValueError:
            self.fail(f"environment variable {key} is malformed: {raw!r}")
            return default

    def mode(self) -> Mode:
        mode = self.get("MODE") or "prod"
        if mode == "dev":
            return "dev"
        if mode != "prod":
            self.fail(f"MODE must be dev or prod, got {mode!r}")
        return "prod"

    def origin_secrets(self, mode: Mode) -> tuple[str, ...]:
        """ORIGIN_SECRET: the current secret, optionally followed by a comma and the previous one during a rotation.
        Required in prod; in dev a request without the header is let through (app.http.origin)."""
        raw = self.get("ORIGIN_SECRET")
        if raw is None:
            if mode == "prod":
                self.fail("ORIGIN_SECRET is required when MODE=prod")
            return ()
        entries = tuple(raw.split(","))
        if "" in entries:
            self.fail("ORIGIN_SECRET has an empty entry")
        elif len(entries) > MAX_ORIGIN_SECRETS:
            self.fail(f"ORIGIN_SECRET holds at most {MAX_ORIGIN_SECRETS} entries: the current and the previous")
        elif not all(_PRINTABLE_ASCII.fullmatch(e) for e in entries):
            self.fail("ORIGIN_SECRET entries must be printable ASCII without spaces")
        elif any(len(e) < MIN_ORIGIN_SECRET_LENGTH for e in entries):
            self.fail(f"ORIGIN_SECRET entries must be at least {MIN_ORIGIN_SECRET_LENGTH} characters")
        else:
            return entries
        return ()

    def app_origin(self) -> str:
        raw = self.required("APP_ORIGIN")
        if not raw:
            return ""
        parts = urllib.parse.urlsplit(raw.lower())
        try:
            port = parts.port
        except ValueError:
            port = -1
        if (
            not raw.isascii()  # browsers send a non-ASCII name in Origin as punycode (xn--)
            or parts.scheme not in _DEFAULT_PORTS
            or not parts.hostname
            or parts.username is not None
            or parts.netloc.endswith(":")  # a colon with no port: browsers never send one
            or f"{parts.scheme}://{parts.netloc}" != raw.lower()
            or port == -1
        ):
            self.fail(f"APP_ORIGIN must be an origin such as https://app.example (scheme://host[:port]), got {raw!r}")
        elif port == _DEFAULT_PORTS[parts.scheme]:
            self.fail(f"APP_ORIGIN must leave out the default port, as browsers do in Origin: got {raw!r}")
        return raw.lower()

    def http_addr(self) -> tuple[str, int]:
        addr = self.get("HTTP_ADDR") or DEFAULT_ADDR
        host, sep, port_text = addr.rpartition(":")
        if not sep or not port_text.isdigit():
            self.fail(f"HTTP_ADDR must be host:port, got {addr!r}")
            return "", 0
        port = int(port_text)
        if not 1 <= port <= MAX_PORT:
            self.fail(f"HTTP_ADDR port must be 1-{MAX_PORT}, got {port}")
        # an IPv6 literal is written in brackets, [::1]:8000, as in a URL
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        return host, port

    def database_url(self, key: str) -> str:
        """A postgres:// URL without a password that either verifies the server or is declared same-host."""
        raw = self.get(key)
        if raw is None:
            self.fail(f"missing required environment variable {key}")
            return ""
        problem = _url_shape_problem(raw) or _url_policy_problem(raw, plaintext=self.get("DATABASE_PLAINTEXT"))
        if problem:
            self.fail(f"{key} {problem}")
            return ""
        return raw


def _url_shape_problem(raw: str) -> str | None:
    scheme, _, _ = raw.partition("://")
    base, plus, driver = scheme.partition("+")
    # a driver suffix such as postgresql+asyncpg:// fails instead of being rewritten, so a stale .env never looks like
    # it works
    if plus:
        return f"must not carry the driver suffix +{driver}; use {base}://"
    try:
        parts = urllib.parse.urlsplit(raw)
        _ = parts.port  # raises ValueError for a malformed port
    except ValueError:
        return "must be a postgres:// or postgresql:// connection string"
    if parts.scheme not in {"postgres", "postgresql"} or not parts.hostname:
        return "must be a postgres:// or postgresql:// connection string"
    if not parts.username or not parts.path.strip("/"):
        return "must name the user and the database"
    return None


def _url_policy_problem(raw: str, *, plaintext: str | None) -> str | None:
    parts = urllib.parse.urlsplit(raw)
    query = urllib.parse.parse_qs(parts.query)
    if parts.password is not None or "password" in query:
        return "must not contain a password; set it in the matching _PASSWORD or _PASSWORD_FILE variable"
    if unknown := sorted(set(query) - _URL_PARAMS):
        return f"has unsupported parameters: {', '.join(unknown)}"
    sslmode = query.get("sslmode", ["prefer"])[0]
    if sslmode not in _SSLMODES:
        return f"has an unknown sslmode {sslmode!r}"
    # The connection verifies the server's certificate (verify-full protects against eavesdropping and a man in the
    # middle) unless DATABASE_PLAINTEXT=same-host declares that the traffic never leaves this host, as on the compose
    # network or with a local database.
    if sslmode != "verify-full" and plaintext != "same-host":
        return "must use sslmode=verify-full; set DATABASE_PLAINTEXT=same-host only when the database runs on this host"
    return None


def _parse_int(text: str) -> int:
    """Digits only: int() would also take 1_0, + and surrounding spaces."""
    if not _INTEGER.fullmatch(text):
        msg = f"not an integer: {text!r}"
        raise ValueError(msg)
    return int(text)


def _parse_bool(text: str) -> bool:
    if text not in _BOOLEANS:
        msg = f"not a boolean: {text!r}"
        raise ValueError(msg)
    return _BOOLEANS[text]


def _parse_duration(text: str) -> float:
    """Seconds in a Go duration such as 5s or 1m30s."""
    if text == "0":
        return 0.0
    parts = _DURATION.findall(text)
    if not parts or "".join(n + u for n, u in parts) != text:
        msg = f"not a duration: {text!r}"
        raise ValueError(msg)
    return sum(float(n) * _UNIT_SECONDS[u] for n, u in parts)
