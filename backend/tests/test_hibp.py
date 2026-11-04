"""The Pwned Passwords range client (app.platform.hibp; Go version: internal/platform/hibp/hibp_test.go) against a
local server, and the breach check's place in choosing a new password (Go version: auth_test.go
TestCreateUserBreachedPassword, whose sign-up half needs the database and is in test_auth_db.py)."""

import asyncio
import hashlib
import http.server
import logging
import threading
import time
from typing import TYPE_CHECKING, ClassVar

import pytest

from app.modules.auth.service import CODE_TOO_COMMON, AuthService
from app.modules.auth.service.passwords import PasswordHasher
from app.platform.errors import AppError
from app.platform.hibp import USER_AGENT, HIBPClient, HIBPError
from authfake import NO_DATABASE
from authtest import CHEAP, FULL_WIDTH, FakeBreach

if TYPE_CHECKING:
    from collections.abc import Generator

PHRASE = "correct horse battery staple"


def range_key(password: str) -> tuple[str, str]:
    digest = hashlib.sha1(password.encode(), usedforsecurity=False).hexdigest().upper()
    return digest[:5], digest[5:]


class RangeAPI(http.server.BaseHTTPRequestHandler):
    status: ClassVar[int] = 200
    body: ClassVar[str] = ""
    requests: ClassVar[list[tuple[str, str | None]]] = []
    agents: ClassVar[list[str | None]] = []

    delay: ClassVar[float] = 0.0

    def do_GET(self) -> None:
        type(self).requests.append((self.path, self.headers.get("Add-Padding")))
        type(self).agents.append(self.headers.get("User-Agent"))
        time.sleep(self.delay)
        self.send_response(self.status)
        self.end_headers()
        self.wfile.write(self.body.encode())


@pytest.fixture
def server() -> Generator[str]:
    RangeAPI.status, RangeAPI.body, RangeAPI.requests, RangeAPI.agents, RangeAPI.delay = 200, "", [], [], 0.0
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeAPI)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def breached(base_url: str, password: str = PHRASE) -> bool:
    return asyncio.run(HIBPClient(base_url).breached(password))


def test_only_the_prefix_leaves_and_a_listed_suffix_is_breached(server: str) -> None:
    prefix, suffix = range_key(PHRASE)
    RangeAPI.body = f"{suffix.lower()}:42\r\n0000000000000000000000000000000000A:0\r\n"
    assert breached(server)
    assert RangeAPI.requests == [(f"/range/{prefix}", "true")]
    assert RangeAPI.agents == [USER_AGENT]  # the API asks clients to identify themselves


def test_the_whole_check_has_a_deadline(server: str) -> None:
    RangeAPI.delay = 1.0
    started = time.monotonic()
    with pytest.raises(HIBPError, match="no answer within"):
        asyncio.run(HIBPClient(server, timeout=0.2).breached(PHRASE))
    assert time.monotonic() - started < 0.9


@pytest.mark.parametrize("body", ["{suffix}:0\r\n", "", "0000000000000000000000000000000000A:3\r\n"])
def test_a_padding_entry_or_an_absent_suffix_is_not_breached(server: str, body: str) -> None:
    RangeAPI.body = body.format(suffix=range_key(PHRASE)[1])
    assert not breached(server)


def test_a_server_error_is_no_answer(server: str) -> None:
    RangeAPI.status = 503
    with pytest.raises(HIBPError, match="503"):
        breached(server)


def test_an_unreachable_server_is_no_answer() -> None:
    srv = http.server.HTTPServer(("127.0.0.1", 0), RangeAPI)
    port = srv.server_port
    srv.server_close()
    with pytest.raises(HIBPError) as err:
        breached(f"http://127.0.0.1:{port}")
    assert range_key(PHRASE)[0] not in str(err.value)


@pytest.mark.parametrize("url", ["ftp://example.com", "api.pwnedpasswords.com"])
def test_the_base_url_is_an_http_origin(url: str) -> None:
    with pytest.raises(ValueError, match="not an http"):
        HIBPClient(url)


def create_user(breach: FakeBreach, password: str) -> None:
    service = AuthService(
        NO_DATABASE,
        hasher=PasswordHasher(CHEAP),
        breach=breach,
    )
    try:
        asyncio.run(service.create_user("alice", password))
    finally:
        service.close()


def test_a_breached_password_is_refused_after_nfkc() -> None:
    breach = FakeBreach(answer=True)
    with pytest.raises(AppError) as err:
        create_user(breach, FULL_WIDTH)
    assert (err.value.pointer, err.value.code) == ("/password", CODE_TOO_COMMON)
    assert breach.checked == ["breached passphrase"]


def test_a_failed_breach_check_lets_the_password_through(caplog: pytest.LogCaptureFixture) -> None:
    # past the check, create_user reaches for the database, which does not exist here
    with caplog.at_level(logging.WARNING), pytest.raises(OSError, match="Connect call failed"):
        create_user(FakeBreach(failing=True), PHRASE)
    assert "breached-password check failed; accepting the password" in caplog.messages
