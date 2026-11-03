"""The Alembic revisions produce the Go version's schema, and the models agree with the revisions."""

import asyncio
import pathlib
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from sqlalchemy import text

from app.main.migrate import alembic_config
from app.main.schema import metadata
from app.platform.db import create_engine

if TYPE_CHECKING:
    from app.platform.config import DatabaseSettings
    from dbtest import Server

pytestmark = pytest.mark.db

GO_MIGRATIONS = sorted((pathlib.Path(__file__).parent / "fixtures/go_migrations").glob("*.sql"))

# Everything in the public schema that defines its shape, as PostgreSQL itself prints it, so two databases built by
# different tools compare equal only when they really are. Alembic's bookkeeping table is in its own schema.
CATALOG = {
    "tables": """
        SELECT c.relname, obj_description(c.oid, 'pg_class')
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
    """,
    "columns": """
        SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
               pg_get_expr(d.adbin, d.adrelid)
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
          AND a.attnum > 0 AND NOT a.attisdropped
    """,
    "constraints": """
        SELECT c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid)
        FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
        WHERE n.nspname = 'public'
    """,
    "indexes": """
        SELECT tablename, indexname, indexdef FROM pg_indexes
        WHERE schemaname = 'public'
    """,
}


async def catalog(settings: DatabaseSettings) -> dict[str, list[tuple[Any, ...]]]:
    engine = create_engine(settings)
    try:
        async with engine.connect() as conn:
            return {
                name: sorted(tuple(row) for row in (await conn.execute(text(query))).all())
                for name, query in CATALOG.items()
            }
    finally:
        await engine.dispose()


async def apply_go_migrations(settings: DatabaseSettings) -> None:
    """Runs each Go migration file as the owner role in its own transaction, as Atlas applies them.

    None of the files carries an Atlas directive (such as txmode none), so running them in file-name order is all
    `atlas migrate apply` does with them.
    """
    engine = create_engine(settings)
    try:
        for path in GO_MIGRATIONS:
            async with engine.begin() as conn:
                raw = await conn.get_raw_connection()
                assert raw.driver_connection is not None
                # the driver's simple query protocol runs a file of several statements in one call
                await raw.driver_connection.execute(path.read_text(encoding="utf-8"))
    finally:
        await engine.dispose()


def test_the_revisions_build_the_go_schema(postgres: Server) -> None:
    async def scenario() -> tuple[dict[str, list[tuple[Any, ...]]], dict[str, list[tuple[Any, ...]]]]:
        go = await postgres.new_database()
        await apply_go_migrations(go.owner)
        ours = await postgres.new_migrated_database()
        return await catalog(go.owner), await catalog(ours.owner)

    go, ours = asyncio.run(scenario())
    assert [t for t, _ in go["tables"]] == [
        "audit_events",
        "items",
        "sessions",
        "sign_in_throttles",
        "trusted_devices",
        "users",
    ]
    for part in CATALOG:
        assert ours[part] == go[part], part


def test_the_models_build_the_schema_the_revisions_build(postgres: Server) -> None:
    # alembic check (below) does not compare CHECK constraints or every default; comparing catalogs covers all of it
    async def scenario() -> tuple[dict[str, list[tuple[Any, ...]]], dict[str, list[tuple[Any, ...]]]]:
        from_models = await postgres.new_database()
        engine = create_engine(from_models.owner)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(metadata().create_all)
        finally:
            await engine.dispose()
        from_revisions = await postgres.new_migrated_database()
        return await catalog(from_models.owner), await catalog(from_revisions.owner)

    from_models, from_revisions = asyncio.run(scenario())
    for part in CATALOG:
        assert from_models[part] == from_revisions[part], part


def test_the_models_match_the_revisions(postgres: Server) -> None:
    async def scenario() -> None:
        db = await postgres.new_migrated_database()
        engine = create_engine(db.owner)
        try:
            async with engine.connect() as conn:
                await conn.run_sync(lambda c: command.check(alembic_config(c)))
        finally:
            await engine.dispose()

    # Overlaps the catalog comparison above on purpose: its message names each difference the way autogenerate sees it.
    # AutogenerateDiffsDetected lists each difference; the fix is a new revision (make -C backend migration MSG=...)
    asyncio.run(scenario())
