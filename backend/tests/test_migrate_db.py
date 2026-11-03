"""`app migrate` against a real PostgreSQL: grants, the append-only rule, retention and re-runs."""

import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.main import cli
from app.main.migrate import MigrateError, migrate
from app.platform.config import DatabaseSettings
from app.platform.db import Database, create_engine
from dbtest import APP_ROLE, OWNER_ROLE

if TYPE_CHECKING:
    from dbtest import Server

pytestmark = pytest.mark.db

USER = "INSERT INTO users (account, password_hash) VALUES ('alice', 'x') RETURNING id"


async def execute(settings: DatabaseSettings, *statements: str) -> list[object]:
    """Runs the statements in one transaction and returns each one's first column of its first row, if any."""
    engine = create_engine(settings)
    try:
        async with engine.begin() as conn:
            results: list[object] = []
            for stmt in statements:
                result = await conn.execute(text(stmt))
                results.append(result.scalar() if result.returns_rows else None)
            return results
    finally:
        await engine.dispose()


def denied(settings: DatabaseSettings, statement: str) -> bool:
    try:
        asyncio.run(execute(settings, statement))
    except DBAPIError as err:
        # both are SQLSTATE 42501 insufficient_privilege; ALTER on another role's table says the second
        return any(text in str(err.orig) for text in ("permission denied", "must be owner"))
    return False


def test_the_app_role_reads_and_writes_but_cannot_change_the_schema(postgres: Server) -> None:
    db = asyncio.run(postgres.new_migrated_database())
    results = asyncio.run(
        execute(
            db.app,
            USER,
            "UPDATE users SET is_disabled = true",
            "INSERT INTO items (title, owner_id) SELECT 'a', id FROM users",
            "DELETE FROM items RETURNING 1",
            "SELECT count(*) FROM users",
        )
    )
    assert results[3:] == [1, 1]
    assert denied(db.app, "CREATE TABLE intruder (id int)")
    # the app role cannot rewrite which revisions ran
    assert denied(db.app, "SELECT * FROM alembic.alembic_version")
    assert denied(db.app, "DELETE FROM alembic.alembic_version")
    assert denied(db.app, "ALTER TABLE users ADD COLUMN extra text")


def test_audit_events_are_append_only_for_the_app_role(postgres: Server) -> None:
    db = asyncio.run(postgres.new_migrated_database())
    insert = "INSERT INTO audit_events (at, action, target_id) VALUES (now(), 'user.disabled', uuidv7())"
    asyncio.run(execute(db.app, insert, "SELECT count(*) FROM audit_events"))
    assert denied(db.app, "UPDATE audit_events SET action = 'user.enabled'")
    assert denied(db.app, "DELETE FROM audit_events")
    assert denied(db.app, "TRUNCATE audit_events")


def test_every_run_deletes_audit_events_past_retention(postgres: Server) -> None:
    db = asyncio.run(postgres.new_migrated_database())
    insert = "INSERT INTO audit_events (at, action, target_id) VALUES (now() - interval '{}', 'user.deleted', uuidv7())"
    asyncio.run(execute(db.owner, insert.format("366 days"), insert.format("364 days")))

    async def rerun() -> str | None:
        engine = create_engine(db.owner)
        try:
            return await migrate(engine, APP_ROLE)
        finally:
            await engine.dispose()

    assert asyncio.run(rerun()) == "baseline"
    (kept,) = asyncio.run(execute(db.owner, "SELECT count(*) FROM audit_events WHERE at > now() - interval '365 days'"))
    (total,) = asyncio.run(execute(db.owner, "SELECT count(*) FROM audit_events"))
    assert (kept, total) == (1, 1)


def test_a_malformed_append_only_comment_fails_the_run(postgres: Server) -> None:
    db = asyncio.run(postgres.new_migrated_database())
    asyncio.run(execute(db.owner, "COMMENT ON TABLE items IS 'append-only: keep forever'"))

    async def rerun() -> None:
        engine = create_engine(db.owner)
        try:
            await migrate(engine, APP_ROLE)
        finally:
            await engine.dispose()

    with pytest.raises(MigrateError, match="table items: comment 'append-only: keep forever'"):
        asyncio.run(rerun())


def test_a_missing_app_role_fails_the_run(postgres: Server) -> None:
    db = asyncio.run(postgres.new_database())

    async def run() -> None:
        engine = create_engine(db.owner)
        try:
            await migrate(engine, "nobody")
        finally:
            await engine.dispose()

    with pytest.raises(MigrateError, match="app role 'nobody' does not exist"):
        asyncio.run(run())


def test_the_command_reads_its_settings_and_can_be_re_run(postgres: Server, capsys: pytest.CaptureFixture[str]) -> None:
    db = asyncio.run(postgres.new_database())
    environ = {
        "MIGRATE_DATABASE_URL": db.owner.url,
        "MIGRATE_DATABASE_PASSWORD": OWNER_ROLE,
        "DB_APP_USER": APP_ROLE,
        "DATABASE_PLAINTEXT": "same-host",
    }
    assert cli.main(["migrate"], environ=environ) == 0
    assert cli.main(["migrate"], environ=environ) == 0
    assert capsys.readouterr().out.count(f"at revision baseline, app role {APP_ROLE} granted") == 2


def test_ping_reaches_the_database_as_the_app_role(postgres: Server) -> None:
    db = asyncio.run(postgres.new_migrated_database())

    async def ping(settings: DatabaseSettings) -> None:
        database = Database.open(settings)
        try:
            await database.ping()
        finally:
            await database.close()

    asyncio.run(ping(db.app))
    wrong_password = DatabaseSettings(url=db.app.url, password=db.app.password + "-wrong")
    with pytest.raises(DBAPIError):
        asyncio.run(ping(wrong_password))
