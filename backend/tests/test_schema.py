"""Checks on the schema's sources that need no database (the db tests are in test_schema_db.py)."""

import ast
import pathlib
import re

from alembic.script import ScriptDirectory

import dbtest
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


def test_the_go_migrations_are_all_there() -> None:
    fixtures = pathlib.Path(__file__).parent / "fixtures/go_migrations"
    assert len(list(fixtures.glob("*.sql"))) == 6


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
