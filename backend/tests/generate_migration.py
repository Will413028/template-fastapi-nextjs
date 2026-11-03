"""`make -C backend migration MSG=<name>`: writes a revision with what the models have and the revisions lack.

Needs Docker: autogenerate compares the models with a real database, so this starts the same PostgreSQL container the
db tests use (dbtest.py), applies the existing revisions, generates the new one into
src/app/main/migrations/versions and stops the container.
"""

import asyncio
import sys

from alembic import command

from app.main.migrate import alembic_config
from app.platform.db import create_engine
from dbtest import Server


async def generate(server: Server, message: str) -> None:
    db = await server.new_migrated_database()
    engine = create_engine(db.owner)
    try:
        async with engine.connect() as conn:
            await conn.run_sync(lambda c: command.revision(alembic_config(c), message=message, autogenerate=True))
    finally:
        await engine.dispose()


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not argv[0]:
        sys.stderr.write("usage: generate_migration.py <message>\n")
        return 2
    server = Server.start()
    try:
        asyncio.run(generate(server, argv[0]))
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
