"""`app migrate`: applies the Alembic revisions as the owner role, then grants the app role DML. Both can be re-run.

A table whose comment starts with "append-only" (the `comment` in its model's __table_args__) gets INSERT and SELECT
only, so the application cannot change or delete its rows; "append-only: retain N days by <column>" also deletes rows
older than N days on every run, as the owner role.
"""

import asyncio
import re
import sys
from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.platform.config import ConfigError, MigrateSettings
from app.platform.db import create_engine

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy import Connection
    from sqlalchemy.ext.asyncio import AsyncEngine

# Alembic's bookkeeping table lives in a schema of its own, owned by the owner role and never granted to the app role,
# so the application cannot rewrite which revisions ran (the Go version keeps Atlas's table out of public likewise).
VERSION_SCHEMA = "alembic"
APPEND_ONLY = re.compile(r"^append-only(?:: retain ([1-9][0-9]*) days by ([a-z_][a-z0-9_]*))?$")


class MigrateError(RuntimeError):
    """The schema or the grants could not be brought up to date."""


def alembic_config(connection: Connection | None = None) -> Config:
    """Alembic's configuration, for a run on `connection` (migrations/env.py reads it from the attributes)."""
    config = Config()
    config.set_main_option("script_location", "app.main:migrations")
    config.set_main_option(
        "file_template", "r%%(year)d%%(month).2d%%(day).2d%%(hour).2d%%(minute).2d%%(second).2d_%%(slug)s"
    )
    if connection is not None:
        config.attributes["connection"] = connection
    return config


async def migrate(engine: AsyncEngine, app_role: str) -> str | None:
    """Upgrades to the newest revision and grants `app_role`; returns the revision the database is at."""
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {VERSION_SCHEMA}"))
    async with engine.connect() as conn:
        await conn.run_sync(lambda c: command.upgrade(alembic_config(c), "head"))
        await conn.commit()
    async with engine.begin() as conn:
        return await conn.run_sync(lambda c: _grant(c, app_role))


def _grant(conn: Connection, role: str) -> str | None:
    """Gives `role` DML on everything in public, now and later, except what the append-only rule takes back.

    Default privileges cover only objects the owner role creates, which is every object the migrations create. The
    caller's transaction holds all of it: a partial grant never commits.
    """
    if conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}).first() is None:
        msg = f"app role {role!r} does not exist"
        raise MigrateError(msg)
    r = conn.dialect.identifier_preparer.quote(role)
    for stmt in (
        f"GRANT USAGE ON SCHEMA public TO {r}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {r}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {r}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {r}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {r}",
    ):
        conn.execute(text(stmt))
    _append_only(conn, r)
    return MigrationContext.configure(conn, opts={"version_table_schema": VERSION_SCHEMA}).get_current_revision()


def _append_only(conn: Connection, role: str) -> None:
    """Takes UPDATE, DELETE and TRUNCATE on every append-only table away from `role` (already quoted) and deletes
    the rows past each table's retention. A comment that starts with "append-only" but does not match the format fails
    the run, so a typo cannot leave a table writable or kept forever.
    """
    rows = conn.execute(
        text("""
            SELECT c.relname, d.description
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_description d ON d.objoid = c.oid AND d.classoid = 'pg_class'::regclass AND d.objsubid = 0
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND d.description LIKE 'append-only%'
            ORDER BY c.relname
        """)
    ).all()
    quote = conn.dialect.identifier_preparer.quote
    for name, comment in rows:
        match = APPEND_ONLY.match(comment)
        if match is None:
            msg = f'table {name}: comment {comment!r} is not "append-only" or "append-only: retain N days by <column>"'
            raise MigrateError(msg)
        table = quote(name)
        days, column = match.groups()
        if days is not None:
            target = sa.table(name, sa.column(column))
            # the arguments are years, months, weeks and days
            cutoff = sa.func.now() - sa.func.make_interval(0, 0, 0, int(days))
            conn.execute(sa.delete(target).where(target.c[column] < cutoff))


async def _run(settings: MigrateSettings) -> str | None:
    engine = create_engine(settings.database)
    try:
        return await migrate(engine, settings.app_role)
    finally:
        await engine.dispose()


def run(environ: Mapping[str, str] | None) -> int:
    try:
        settings = MigrateSettings.from_env(environ)
    except ConfigError as err:
        sys.stderr.write(f"migrate: {err}\n")
        return 1
    try:
        revision = asyncio.run(_run(settings))
    except (MigrateError, SQLAlchemyError, OSError) as err:
        # SQLAlchemy prints URLs with the password masked, and asyncpg's errors carry no password
        sys.stderr.write(f"migrate: {err}\n")
        return 1
    sys.stdout.write(f"migrate: at revision {revision}, app role {settings.app_role} granted\n")
    return 0
