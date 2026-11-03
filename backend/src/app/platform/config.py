"""Settings read once at startup. The only module that reads environment variables (ruff TID251 bans it elsewhere)."""

import ipaddress
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Self

if TYPE_CHECKING:
    from collections.abc import Mapping

type Mode = Literal["dev", "prod"]

# An empty host binds every interface, as HTTP_ADDR=":8000" does for the Go version this template mirrors.
DEFAULT_ADDR = ":8000"
MAX_PORT = 65535


class ConfigError(ValueError):
    """A setting is missing or malformed; the process must not start."""


@dataclass(frozen=True, slots=True)
class Settings:
    mode: Mode
    host: str
    port: int

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Self:
        env = os.environ if environ is None else environ
        mode = env.get("MODE", "prod")
        if mode not in ("dev", "prod"):
            msg = f"MODE must be dev or prod, got {mode!r}"
            raise ConfigError(msg)
        host, port = _parse_addr(env.get("HTTP_ADDR", DEFAULT_ADDR))
        return cls(mode=mode, host=host, port=port)

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


def _parse_addr(addr: str) -> tuple[str, int]:
    host, sep, port_text = addr.rpartition(":")
    if not sep or not port_text.isdigit():
        msg = f"HTTP_ADDR must be host:port, got {addr!r}"
        raise ConfigError(msg)
    port = int(port_text)
    if not 1 <= port <= MAX_PORT:
        msg = f"HTTP_ADDR port must be 1-{MAX_PORT}, got {port}"
        raise ConfigError(msg)
    # an IPv6 literal is written in brackets, [::1]:8000, as in a URL
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host, port
