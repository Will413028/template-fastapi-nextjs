import http.server
import importlib
import threading
from typing import TYPE_CHECKING

import pytest

from app.main import cli
from app.main.routes import RouteContractError

if TYPE_CHECKING:
    import pathlib
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


@pytest.mark.parametrize("command", ["serve", "migrate", "seed-admin", "recover-admin"])
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


class ServerStartedError(AssertionError):
    """`app serve` got as far as starting the server."""


@pytest.fixture(autouse=True)
def no_server(monkeypatch: pytest.MonkeyPatch) -> None:
    # A serve test checks that startup is refused. Should a regression let it through, the real server would block the
    # test run forever (it did while ORIGIN_SECRET was not yet required); this makes it a failure instead.
    def run(*_: object, **__: object) -> None:
        raise ServerStartedError

    monkeypatch.setattr("uvicorn.run", run)


SERVE_ENV = {
    "MODE": "prod",
    "HTTP_ADDR": "127.0.0.1:18742",
    "DATABASE_URL": "postgres://app@127.0.0.1:1/app",
    "DATABASE_PASSWORD": "x",
    "DATABASE_PLAINTEXT": "same-host",
    "ORIGIN_SECRET": "s" * 43,
    "APP_ORIGIN": "https://app.example",
}


def test_serve_with_routes_that_disagree_with_the_contract_does_not_start(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def disagree(*_: object, **__: object) -> None:
        msg = "GET /api/users/me has no route"
        raise RouteContractError(msg)

    monkeypatch.setattr("app.main.factory.check_routes", disagree)
    assert cli.main(["serve"], environ=SERVE_ENV) == 1
    assert "serve: routes and contract disagree: GET /api/users/me has no route" in capsys.readouterr().err


def test_serve_in_prod_without_the_origin_secret_does_not_start(capsys: pytest.CaptureFixture[str]) -> None:
    env = {k: v for k, v in SERVE_ENV.items() if k != "ORIGIN_SECRET"}
    assert cli.main(["serve"], environ=env) == 1
    assert "ORIGIN_SECRET is required when MODE=prod" in capsys.readouterr().err


def test_serve_without_the_contract_fails_cleanly(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)  # no api/openapi.yaml here
    assert cli.main(["serve"], environ=SERVE_ENV) == 1
    assert "serve: load the contract" in capsys.readouterr().err


def test_serve_with_an_invalid_contract_fails_cleanly(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "openapi.yaml").write_text("openapi: 3.0.3\npaths: nope\n")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["serve"], environ=SERVE_ENV) == 1
    assert "serve: load the contract" in capsys.readouterr().err
