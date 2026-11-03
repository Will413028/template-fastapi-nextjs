"""The real API over a migrated test database, for the auth tests (Go version: internal/httpapi/auth_test.go
newAuthAPI and its helpers). The database tests run only in CI (make test-db)."""

import asyncio
import contextlib
import dataclasses
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, override

from sqlalchemy import text

from apitest import PEER, checked_client, contract, deps
from app.main.factory import Modules, create_app
from app.modules.auth.dependencies import Auth, SessionCookie
from app.modules.auth.service import AuthService
from app.modules.auth.service.passwords import Argon2Params, PasswordHasher
from app.modules.items.service import ItemsService
from app.platform.db import Database, create_engine
from app.web.origin import OriginPolicy
from app.web.ratelimit import IPLimiter

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Generator

    import httpx2
    from fastapi.testclient import TestClient

    from app.platform.config import DatabaseSettings
    from dbtest import Server, TestDatabase

COOKIE = "__Host-session"
# the Go version's goodPassword; a name ruff S105 does not read as a hardcoded secret
LONG_ENOUGH = "a long enough password"
SECRET = "s" * 43
START = datetime(2026, 10, 18, 12, tzinfo=UTC)
# keeps the tests fast; production uses OWASP's parameters (passwords.OWASP_ARGON2)
CHEAP = Argon2Params(memory_kib=64, time_cost=1, parallelism=1)


class Clock:
    """The service's clock: stopped, moved by the test."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._now = START

    def __call__(self) -> datetime:
        with self._lock:
            return self._now

    def advance(self, by: timedelta) -> None:
        with self._lock:
            self._now += by


class CountingHasher(PasswordHasher):
    """Counts hashes and verifications, and can make each verification slower."""

    def __init__(self) -> None:
        super().__init__(CHEAP)
        self._lock = threading.Lock()
        self.hashes = 0
        self.verifies = 0
        self.delay = 0.0

    @override
    async def hash(self, password: str) -> str:
        with self._lock:
            self.hashes += 1
        return await super().hash(password)

    @override
    async def verify(self, password: str, encoded: str) -> bool:
        with self._lock:
            self.verifies += 1
        await asyncio.sleep(self.delay)
        return await super().verify(password, encoded)


async def execute(settings: DatabaseSettings, statement: str, **params: object) -> list[Any]:
    """Runs one statement in its own connection and returns its rows."""
    engine = create_engine(settings)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(statement), params)
            return list(result) if result.returns_rows else []
    finally:
        await engine.dispose()


def set_cookie(response: httpx2.Response, name: str = COOKIE) -> list[dict[str, str]]:
    """Each Set-Cookie for name, as its attributes: "value" and the lowercased attribute names."""
    found: list[dict[str, str]] = []
    for header in response.headers.get_list("set-cookie"):
        first, *attributes = header.split("; ")
        cookie_name, _, value = first.partition("=")
        if cookie_name != name:
            continue
        parsed = {"value": value}
        for attribute in attributes:
            key, _, attribute_value = attribute.partition("=")
            parsed[key.lower()] = attribute_value
        found.append(parsed)
    return found


def session_token(response: httpx2.Response) -> str:
    """The one session cookie response sets (with a lifetime: not a clearing one)."""
    issued = [c for c in set_cookie(response) if int(c.get("max-age", "0")) > 0]
    assert len(issued) == 1, response.headers.get_list("set-cookie")
    return issued[0]["value"]


@dataclass
class AuthAPI:
    client: TestClient
    service: AuthService
    db: TestDatabase
    clock: Clock
    hasher: CountingHasher

    def do(
        self,
        method: str,
        path: str,
        token: str = "",
        *,
        json: object = None,
        headers: dict[str, str] | None = None,
    ) -> httpx2.Response:
        """A same-origin request with the session cookie when token is not empty. Every 201 it gets back is checked
        by follow_location."""
        sent = {"Sec-Fetch-Site": "same-origin"} | (headers or {})
        if token:
            sent["Cookie"] = f"{COOKIE}={token}"
        response = self.client.request(method, path, json=json, headers=sent)
        self.client.cookies.clear()  # each request carries only the cookie the test gives it
        self.follow_location(response, token)
        return response

    def follow_location(self, response: httpx2.Response, token: str) -> None:
        """A 201 has a Location, and GET on it with the creator's cookie (the session the response sets, else the
        one the request carried) is a 200 (docs/adr/2026-10-18-api-and-auth-baseline.md D9)."""
        if response.status_code != 201:
            return
        location = response.headers.get("location")
        assert location, f"201 without Location: {response.text}"
        issued = [c["value"] for c in set_cookie(response) if int(c.get("max-age", "0")) > 0]
        got = self.do("GET", location, issued[0] if issued else token)
        assert got.status_code == 200, f"GET {location}, the Location of a 201, = {got.status_code} {got.text}"

    def sign_up(self, account: str, password: str = LONG_ENOUGH) -> httpx2.Response:
        return self.do("POST", "/api/users", json={"account": account, "password": password})

    def sign_in(
        self, account: str, password: str = LONG_ENOUGH, token: str = "", *, address: str | None = None
    ) -> httpx2.Response:
        """Sign-in from the test client's address, or from address as the site reports it (X-Real-IP)."""
        headers = {"X-Origin-Secret": SECRET, "X-Real-IP": address} if address else None
        return self.do("POST", "/api/sessions", token, json={"account": account, "password": password}, headers=headers)

    def me(self, token: str) -> httpx2.Response:
        return self.do("GET", "/api/users/me", token)

    def must_sign_up(self, account: str) -> tuple[dict[str, Any], str]:
        response = self.sign_up(account)
        assert response.status_code == 201, f"sign up {account} = {response.status_code} {response.text}"
        return response.json(), session_token(response)

    def must_sign_in(self, account: str, password: str = LONG_ENOUGH) -> tuple[dict[str, Any], str]:
        response = self.sign_in(account, password)
        assert response.status_code == 201, f"sign in {account} = {response.status_code} {response.text}"
        return response.json(), session_token(response)

    def sql(self, statement: str, **params: object) -> list[Any]:
        """Runs statement as the app role, outside the API."""
        return asyncio.run(execute(self.db.app, statement, **params))

    def call[T](self, coroutine_function: Callable[..., Awaitable[T]], *args: object) -> T:
        """Calls a service method on the app's event loop, where its connection pool lives."""
        portal = self.client.portal
        assert portal is not None
        return portal.call(coroutine_function, *args)


@contextlib.contextmanager
def auth_api(
    postgres: Server,
    *,
    per_min: int = 1000,
    absolute: timedelta = timedelta(days=7),
    idle: timedelta = timedelta(days=1),
) -> Generator[AuthAPI]:
    """The real app over a new migrated database. Sign-up and sign-in are limited to per_min per address on a stopped
    clock; the site's secret is configured but not required, so a test can send X-Real-IP with it."""
    db = asyncio.run(postgres.new_migrated_database())
    database = Database.open(db.app)
    clock = Clock()
    hasher = CountingHasher()
    service = AuthService(database.engine, session_absolute=absolute, session_idle=idle, hasher=hasher, now=clock)
    app = create_app(
        dataclasses.replace(
            deps(contract()),
            database=database,
            origin=OriginPolicy(secrets=(SECRET,), required=False),
            limiter=IPLimiter(per_min, lambda: 1_000_000.0),
            modules=Modules(
                auth=Auth(service, SessionCookie(name=COOKIE, secure=True)), items=ItemsService(database.engine)
            ),
        )
    )
    with checked_client(app, against=contract(), peer=PEER) as client:
        yield AuthAPI(client=client, service=service, db=db, clock=clock, hasher=hasher)
