"""Checks on the schema's sources that need no database (the db tests are in test_schema_db.py)."""

import ast
import asyncio
import pathlib
import re

import pytest
from alembic.script import ScriptDirectory

import dbtest
import revision_sql
from app.main.migrate import alembic_config
from app.main.schema import MODEL_MODULES, metadata

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
VERSIONS = SRC / "app/main/migrations/versions"


def test_every_module_with_tables_is_registered() -> None:
    found = {
        ".".join(path.relative_to(SRC).with_suffix("").parts) for path in (SRC / "app/modules").glob("*/models.py")
    }
    assert found == set(MODEL_MODULES)


def test_the_test_image_is_the_compose_image() -> None:
    compose = (dbtest.REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert re.findall(r"image: (postgres:\S+)", compose) == [dbtest.IMAGE]


def test_the_revisions_form_one_line_from_the_baseline() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    assert script.get_heads() == [script.get_current_head()]
    assert script.get_base() == "baseline"
    assert all(rev.down_revision is None or isinstance(rev.down_revision, str) for rev in script.walk_revisions())


def test_revisions_are_forward_only() -> None:
    for path in VERSIONS.glob("*.py"):
        names = {
            node.name for node in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(node, ast.FunctionDef)
        }
        assert "downgrade" not in names, path.name


def test_foreign_keys_to_users_allow_deleting_the_account() -> None:
    # NO ACTION or RESTRICT would make deleting an account fail (docs/rules/database-design.md, Foreign Key)
    to_users = [
        fk for table in metadata().tables.values() for fk in table.foreign_keys if fk.column.table.name == "users"
    ]
    assert to_users
    for fk in to_users:
        assert fk.ondelete in {"CASCADE", "SET NULL"}, fk.parent


def test_waiting_for_postgres_retries_until_it_accepts() -> None:
    attempts: list[int] = []

    async def connect() -> None:
        attempts.append(1)
        if len(attempts) < 3:
            msg = "the database system is starting up"
            raise ConnectionRefusedError(msg)

    asyncio.run(dbtest.wait_until_accepting(connect, within=5, interval=0))
    assert len(attempts) == 3


def test_waiting_for_postgres_gives_up_after_the_timeout() -> None:
    async def connect() -> None:
        msg = "the database system is starting up"
        raise ConnectionRefusedError(msg)

    with pytest.raises(ConnectionRefusedError):
        asyncio.run(dbtest.wait_until_accepting(connect, within=0, interval=0))


def test_each_revision_renders_to_its_own_sql_file_for_squawk(tmp_path: pathlib.Path) -> None:
    (baseline,) = revision_sql.write(tmp_path)
    sql = baseline.read_text(encoding="utf-8")
    assert baseline.name == "r20261103_baseline.sql"
    assert sql.startswith("BEGIN;")
    assert sql.rstrip().endswith("COMMIT;")
    assert "CREATE TABLE users" in sql
    # Alembic's own bookkeeping table is cut out; recording the revision stays
    assert "CREATE TABLE alembic.alembic_version" not in sql
    assert "INSERT INTO alembic.alembic_version" in sql
