from typing import TYPE_CHECKING

import pytest

from app.platform.config import ConfigError, HttpSettings, MigrateSettings, Settings

if TYPE_CHECKING:
    import pathlib

DB_URL = "postgres://app@db.example:5432/app?sslmode=verify-full"
SERVE = {"DATABASE_URL": DB_URL, "DATABASE_PASSWORD": "pw"}


def test_serve_defaults_are_production() -> None:
    settings = Settings.from_env(SERVE)
    assert settings.mode == "prod"
    assert (settings.http.host, settings.http.port) == ("", 8000)
    assert settings.http.probe_host == "127.0.0.1"
    db = settings.database
    assert (db.url, db.password, db.max_conns, db.statement_timeout, db.lock_timeout) == (DB_URL, "pw", 20, 5.0, 3.0)
    assert db.sslmode == "verify-full"


def test_dev_mode_address_and_pool() -> None:
    env = SERVE | {"MODE": "dev", "HTTP_ADDR": "127.0.0.1:9000", "DB_MAX_CONNS": "5", "DB_STATEMENT_TIMEOUT": "1m30s"}
    settings = Settings.from_env(env)
    assert settings.mode == "dev"
    assert (settings.http.host, settings.http.port, settings.http.probe_host) == ("127.0.0.1", 9000, "127.0.0.1")
    assert (settings.database.max_conns, settings.database.statement_timeout) == (5, 90.0)


def test_healthcheck_needs_only_the_address() -> None:
    assert HttpSettings.from_env({"HTTP_ADDR": "[::]:8001"}) == HttpSettings(host="::", port=8001)
    assert HttpSettings.from_env({"HTTP_ADDR": "[::]:8001"}).probe_host == "127.0.0.1"


def test_a_value_can_come_from_a_file(tmp_path: pathlib.Path) -> None:
    secret = tmp_path / "db_app_password"
    secret.write_text("from-file\n", encoding="utf-8")
    env = {"DATABASE_URL": DB_URL, "DATABASE_PASSWORD_FILE": str(secret)}
    assert Settings.from_env(env).database.password == secret.read_text(encoding="utf-8").strip()


def test_same_host_allows_a_plaintext_connection() -> None:
    url = "postgres://app@postgres:5432/app?sslmode=disable"
    env = {"DATABASE_URL": url, "DATABASE_PASSWORD": "pw", "DATABASE_PLAINTEXT": "same-host"}
    assert Settings.from_env(env).database.sslmode == "disable"


def test_migrate_connects_as_the_owner_without_a_statement_timeout() -> None:
    env = {"MIGRATE_DATABASE_URL": DB_URL, "MIGRATE_DATABASE_PASSWORD": "owner-pw", "DB_APP_USER": "app"}
    settings = MigrateSettings.from_env(env)
    assert settings.app_role == "app"
    assert (settings.database.password, settings.database.statement_timeout) == ("owner-pw", None)
    assert settings.database.lock_timeout == 5.0


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"MODE": "staging"}, "MODE must be dev or prod"),
        ({"HTTP_ADDR": "8000"}, "HTTP_ADDR must be host:port"),
        ({"HTTP_ADDR": ":http"}, "HTTP_ADDR must be host:port"),
        ({"HTTP_ADDR": ":70000"}, "HTTP_ADDR port must be 1-65535"),
        ({"DATABASE_URL": ""}, "missing required environment variable DATABASE_URL"),
        ({"DATABASE_PASSWORD": ""}, r"missing required environment variable DATABASE_PASSWORD \(or DATABASE_PASSWORD"),
        ({"DATABASE_PASSWORD_FILE": "/nonexistent/pw"}, "set only one of DATABASE_PASSWORD and DATABASE_PASSWORD_FILE"),
        ({"DATABASE_URL": "postgresql+asyncpg://app@db/app"}, r"must not carry the driver suffix \+asyncpg"),
        ({"DATABASE_URL": "mysql://app@db/app"}, "must be a postgres:// or postgresql:// connection string"),
        ({"DATABASE_URL": "postgres://app@db:port/app"}, "must be a postgres:// or postgresql:// connection string"),
        ({"DATABASE_URL": "postgres://app:secret@db/app?sslmode=verify-full"}, "must not contain a password"),
        ({"DATABASE_URL": "postgres://app@db/app?sslmode=verify-full&password=x"}, "must not contain a password"),
        ({"DATABASE_URL": "postgres://db/app?sslmode=verify-full"}, "must name the user and the database"),
        ({"DATABASE_URL": "postgres://app@db?sslmode=verify-full"}, "must name the user and the database"),
        ({"DATABASE_URL": "postgres://app@db/app?sslmode=verify-full&connect_timeout=3"}, "unsupported parameters"),
        ({"DATABASE_URL": "postgres://app@db/app?sslmode=strict"}, "unknown sslmode 'strict'"),
        ({"DATABASE_URL": "postgres://app@db/app?sslmode=disable"}, "must use sslmode=verify-full"),
        ({"DATABASE_URL": "postgres://app@db/app"}, "must use sslmode=verify-full"),
        ({"DB_MAX_CONNS": "0"}, "DB_MAX_CONNS must be positive"),
        ({"DB_MAX_CONNS": "many"}, "DB_MAX_CONNS is malformed"),
        ({"DB_STATEMENT_TIMEOUT": "5"}, "DB_STATEMENT_TIMEOUT is malformed"),
        ({"DB_STATEMENT_TIMEOUT": "5s "}, "DB_STATEMENT_TIMEOUT is malformed"),
        ({"DB_STATEMENT_TIMEOUT": "0s"}, "DB_STATEMENT_TIMEOUT must be positive"),
    ],
)
def test_invalid_values_stop_startup(env: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings.from_env(SERVE | env)


def test_every_problem_is_reported_at_once() -> None:
    with pytest.raises(ConfigError) as exc:
        Settings.from_env({"MODE": "staging", "DB_MAX_CONNS": "0"})
    message = str(exc.value)
    for problem in ("MODE must be dev or prod", "DATABASE_URL", "DATABASE_PASSWORD", "DB_MAX_CONNS must be positive"):
        assert problem in message


def test_an_unreadable_file_names_the_variable(tmp_path: pathlib.Path) -> None:
    env = {"DATABASE_URL": DB_URL, "DATABASE_PASSWORD_FILE": str(tmp_path / "missing")}
    with pytest.raises(ConfigError, match="read DATABASE_PASSWORD_FILE"):
        Settings.from_env(env)


def test_migrate_needs_the_app_role() -> None:
    with pytest.raises(ConfigError, match="DB_APP_USER"):
        MigrateSettings.from_env({"MIGRATE_DATABASE_URL": DB_URL, "MIGRATE_DATABASE_PASSWORD": "pw"})
