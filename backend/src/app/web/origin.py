"""Where a request comes from (D6 in docs/plans/2026-11-03-fastapi-template.md, replacing the Go version's
TRUSTED_PROXIES).

The backend is reachable through the Cloudflare Tunnel by anyone who learns its address, so no header is believed
just because it arrived. Vercel's rewrite and the frontend's server-side calls add X-Origin-Secret; only a request
carrying a configured secret counts as coming through the site, and only then is its X-Real-IP (set by Vercel, which
overwrites what a client sends) the client address. Otherwise the client address is the connection's other end.

ClientMiddleware resolves that before anything else runs, so the access log and the rate limits see the same address;
OriginGuardMiddleware turns away what must not be served, inside the middleware that add the request id and the
security headers and log the request.
"""

import contextvars
import enum
import hmac
import ipaddress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.web.problems import FORBIDDEN, NOT_FOUND, problem_response

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_SECRET_HEADER = b"x-origin-secret"
_REAL_IP_HEADER = b"x-real-ip"


class Origin(enum.Enum):
    VERIFIED = enum.auto()  # a configured secret
    ABSENT = enum.auto()  # no X-Origin-Secret
    FORGED = enum.auto()  # an X-Origin-Secret that is not a configured secret


@dataclass(frozen=True, slots=True)
class OriginPolicy:
    """secrets are the configured ORIGIN_SECRET values (the current one and, during a rotation, the previous one).
    required (MODE=prod) refuses an /api request without the header; MODE=dev lets it through because next dev's
    rewrite cannot add one. A header that is sent must match in either mode."""

    secrets: tuple[str, ...]
    required: bool
    _encoded: tuple[bytes, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.required and not self.secrets:
            msg = "an origin policy that requires the secret needs at least one secret"
            raise ValueError(msg)
        object.__setattr__(self, "_encoded", tuple(s.encode("ascii") for s in self.secrets))

    def check(self, sent: list[bytes]) -> Origin:
        if not sent:
            return Origin.ABSENT
        if len(sent) > 1:
            return Origin.FORGED
        # Bytes, so a non-ASCII header is simply unequal (compare_digest refuses non-ASCII str). Every secret is
        # compared, without stopping at a match: the time taken does not tell which matched.
        matched = False
        for secret in self._encoded:
            matched |= hmac.compare_digest(sent[0], secret)
        return Origin.VERIFIED if matched else Origin.FORGED


@dataclass(frozen=True, slots=True)
class Client:
    address: IPAddress | None  # the client's address: None when the connection's is not an IP address
    peer: IPAddress | None  # the connection's other end
    origin: Origin


_client: contextvars.ContextVar[Client | None] = contextvars.ContextVar("client", default=None)


def current_client() -> Client | None:
    """The client of the request being served; None outside one."""
    return _client.get()


def parse_address(text: str) -> IPAddress | None:
    """text as an IP address, an IPv4-mapped IPv6 address as its IPv4 address; None when it is not one."""
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _header(scope: Scope, name: bytes) -> list[bytes]:
    return [value for key, value in scope["headers"] if key == name]


def resolve(scope: Scope, policy: OriginPolicy) -> Client:
    client: tuple[str, int] | None = scope.get("client")
    peer = parse_address(client[0]) if client else None
    origin = policy.check(_header(scope, _SECRET_HEADER))
    address = peer
    real_ip = _header(scope, _REAL_IP_HEADER)
    if origin is Origin.VERIFIED and len(real_ip) == 1:
        # latin-1 decodes any bytes; a value that is not an address falls back to the connection's
        address = parse_address(real_ip[0].decode("latin-1")) or peer
    return Client(address=address, peer=peer, origin=origin)


class ClientMiddleware:
    """Resolves the request's Client (current_client) for everything inside it."""

    def __init__(self, app: ASGIApp, policy: OriginPolicy) -> None:
        self.app = app
        self.policy = policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = _client.set(resolve(scope, self.policy))
        try:
            await self.app(scope, receive, send)
        finally:
            _client.reset(token)


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


class OriginGuardMiddleware:
    """An /api request without a configured secret gets a 403 problem (no code: the contract's code list stays as it
    is), and the health checks answer only loopback (`app healthcheck` inside the container): from anywhere else they
    are not found, whatever the tunnel's routes forward. Other paths have no route and are not found anyway."""

    def __init__(self, app: ASGIApp, policy: OriginPolicy) -> None:
        self.app = app
        self.policy = policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        client = current_client()
        if client is None:
            msg = "OriginGuardMiddleware runs inside ClientMiddleware"
            raise RuntimeError(msg)
        path: str = scope["path"]
        if _under(path, "/health") and not (client.peer is not None and client.peer.is_loopback):
            await problem_response(NOT_FOUND)(scope, receive, send)
            return
        if _under(path, "/api") and self._refused(client.origin):
            await problem_response(FORBIDDEN)(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _refused(self, origin: Origin) -> bool:
        return origin is Origin.FORGED or (origin is Origin.ABSENT and self.policy.required)
