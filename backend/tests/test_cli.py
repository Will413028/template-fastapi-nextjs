import http.server
import importlib
import threading
from typing import TYPE_CHECKING

import pytest

from app.main import cli

if TYPE_CHECKING:
    from collections.abc import Iterator


class _Handler(http.server.BaseHTTPRequestHandler):
    status = 200

    def do_GET(self) -> None:
        self.send_response(self.status if self.path == "/health/ready" else 404)
        self.end_headers()


@pytest.fixture
def server() -> Iterator[http.server.HTTPServer]:
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_healthcheck_succeeds_when_ready_is_200(server: http.server.HTTPServer) -> None:
    _Handler.status = 200
    assert cli.main(["healthcheck"], environ={"HTTP_ADDR": f":{server.server_port}"}) == 0


def test_healthcheck_fails_when_ready_is_503(server: http.server.HTTPServer) -> None:
    _Handler.status = 503
    assert cli.main(["healthcheck"], environ={"HTTP_ADDR": f":{server.server_port}"}) == 1


def test_healthcheck_fails_when_nothing_listens() -> None:
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_port
    srv.server_close()
    assert cli.main(["healthcheck"], environ={"HTTP_ADDR": f"127.0.0.1:{port}"}) == 1


def test_healthcheck_fails_on_a_malformed_address(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["healthcheck"], environ={"HTTP_ADDR": "8000"}) == 1
    assert "HTTP_ADDR must be host:port" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["serve", "migrate"])
def test_missing_settings_stop_the_command(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([command], environ={}) == 1
    assert "missing required environment variable" in capsys.readouterr().err


def test_unknown_command_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["no-such-command"], environ={})
    assert exc.value.code == 2


def test_every_command_resolves() -> None:
    for module in cli.COMMANDS.values():
        assert callable(importlib.import_module(module).run)
