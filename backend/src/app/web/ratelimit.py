"""Per-address rate limits for the operations that guess or create (Go version: internal/httpapi/ratelimit.go).

An operation opts in with `dependencies=[RATE_LIMITED]`. A dependency runs after ContractValidation, so a
request the contract rejects uses no token, as in the Go version.
"""

import ipaddress
import threading
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from fastapi import Depends, Request

from app.platform.errors import AppError, Kind
from app.web.origin import current_client

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.web.origin import IPAddress

# The time an empty bucket takes to fill again, and how often idle buckets are dropped.
_REFILL_SECONDS = 60.0


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated: float
    last_seen: float


class IPLimiter:
    """An in-memory token bucket per client: per_min requests a minute, bursting up to per_min. An IPv6 client is keyed
    by its /64, the smallest block ISPs assign to one subscriber; keying by the full address would give a single
    subscriber 2^64 buckets. Clients whose address is unknown share one bucket. It holds per process; a second
    backend replica would double the limit.

    now is a monotonic clock in seconds."""

    def __init__(self, per_min: int, now: Callable[[], float]) -> None:
        if per_min < 1:
            msg = "per_min must be positive"
            raise ValueError(msg)
        self._burst = float(per_min)
        self._interval = _REFILL_SECONDS / per_min  # seconds per token
        self._now = now
        self._lock = threading.Lock()
        self._buckets: dict[IPAddress | None, _Bucket] = {}
        self._last_sweep = now()

    def wait(self, address: IPAddress | None) -> timedelta:
        """Takes a token for address and returns zero, or, when its bucket is empty, how long until one is
        available (taking nothing)."""
        now = self._now()
        with self._lock:
            self._sweep(now)
            key = _limit_key(address)
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = self._buckets[key] = _Bucket(tokens=self._burst, updated=now, last_seen=now)
            bucket.tokens = min(self._burst, bucket.tokens + (now - bucket.updated) / self._interval)
            bucket.updated = now
            bucket.last_seen = now
            if bucket.tokens >= 1:
                bucket.tokens -= 1
                return timedelta(0)
            return timedelta(seconds=(1 - bucket.tokens) * self._interval)

    def tracks(self, address: IPAddress | None) -> bool:
        """Whether address has a bucket (one it used within the last refill, or since the last sweep)."""
        with self._lock:
            return _limit_key(address) in self._buckets

    def _sweep(self, now: float) -> None:
        """Drops buckets idle for a refill or longer: they are full again, the same as a new one, so dropping them
        changes no outcome. It runs at most once a refill, inside wait, so no background task is needed."""
        if now - self._last_sweep < _REFILL_SECONDS:
            return
        self._last_sweep = now
        idle = [key for key, bucket in self._buckets.items() if now - bucket.last_seen >= _REFILL_SECONDS]
        for key in idle:
            del self._buckets[key]


def _limit_key(address: IPAddress | None) -> IPAddress | None:
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return address.ipv4_mapped
        return ipaddress.ip_network((address, 64), strict=False).network_address
    return address


async def _rate_limit(request: Request) -> None:
    """The dependency that limits an operation per client address (app.web.origin), with the app's IPLimiter
    (app.main.factory): over the limit, a 429 problem with Retry-After."""
    limiter: IPLimiter = request.app.state.ip_limiter
    client = current_client()
    wait = limiter.wait(client.address if client else None)
    if wait > timedelta(0):
        raise AppError(Kind.RATE_LIMITED, "", "too many requests from this address", retry_after=wait)


RATE_LIMITED = Depends(_rate_limit)
