"""PostgreSQL for the `db` tests, set up the way the compose stack is.

The same image, the same initdb script creating the owner and app roles (deploy/postgres/initdb/10-roles.sh), and the
revisions applied by app.main.migrate. Tests connect as the app role, so a query that only works with more privileges
fails here as it would in production. One container per test run; each test gets its own database. Docker being
unavailable fails the tests instead of skipping them.

These tests run only in CI (`make test-db`); this machine's `make check` leaves them out (-m "not db").
"""

import itertools
import pathlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from sqlalchemy import text
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from app.main.migrate import migrate
from app.platform.config import DatabaseSettings
from app.platform.db import create_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

REPO = pathlib.Path(__file__).resolve().parents[2]
# The PostgreSQL image compose runs, pinned by digest; tests/test_schema.py keeps the two equal, since Dependabot
# updates only the compose file.
IMAGE = "postgres:18.6@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336"
TEMPLATE_DB = "template_fastapi_nextjs"
SUPERUSER = "postgres"
OWNER_ROLE = "template_fastapi_nextjs_owner"
APP_ROLE = "template_fastapi_nextjs_app"
STARTUP_TIMEOUT_SECONDS = 120
# The image starts a temporary server for the initdb scripts, stops it and starts the real one, so the message appears
# twice. The pattern itself requires both: LogMessageWaitStrategy returns on the first match and ignores its times=.
READY = re.compile(r"ready to accept connections.*ready to accept connections", re.DOTALL)


@dataclass(frozen=True, slots=True)
class TestDatabase:
    name: str
    # each role's password equals its name
    owner: DatabaseSettings
    app: DatabaseSettings


class Server:
    def __init__(self, container: DockerContainer) -> None:
        self.container = container
        self.host = container.get_container_host_ip()
        self.port = container.get_exposed_port(5432)
        self._seq = itertools.count(1)

    @classmethod
    def start(cls) -> Self:
        container = (
            DockerContainer(IMAGE)
            .with_envs(
                POSTGRES_USER=SUPERUSER,
                POSTGRES_PASSWORD=SUPERUSER,
                POSTGRES_DB=TEMPLATE_DB,
                DB_OWNER_USER=OWNER_ROLE,
                DB_APP_USER=APP_ROLE,
            )
            .with_exposed_ports(5432)
            .with_copy_into_container(
                REPO / "deploy/postgres/initdb/10-roles.sh", "/docker-entrypoint-initdb.d/10-roles.sh", 0o755
            )
            .with_copy_into_container(f"{OWNER_ROLE}\n".encode(), "/run/secrets/db_owner_password", 0o444)
            .with_copy_into_container(f"{APP_ROLE}\n".encode(), "/run/secrets/db_app_password", 0o444)
            .waiting_for(LogMessageWaitStrategy(READY).with_startup_timeout(STARTUP_TIMEOUT_SECONDS))
        )
        container.start()
        return cls(container)

    def stop(self) -> None:
        self.container.stop()

    def settings(self, database: str, role: str) -> DatabaseSettings:
        return DatabaseSettings(
            url=f"postgres://{role}@{self.host}:{self.port}/{database}?sslmode=disable", password=role
        )

    def superuser_engine(self) -> AsyncEngine:
        return create_engine(self.settings("postgres", SUPERUSER)).execution_options(isolation_level="AUTOCOMMIT")

    async def new_database(self) -> TestDatabase:
        """An empty database configured like the one the initdb script set up.

        The template copies what is inside the database (public owned by the owner role, no PUBLIC access);
        database-level settings are not copied, so they are repeated here.
        """
        name = f"t_{next(self._seq)}"
        engine = self.superuser_engine()
        try:
            async with engine.connect() as conn:
                for stmt in (
                    f'CREATE DATABASE "{name}" TEMPLATE "{TEMPLATE_DB}" OWNER "{OWNER_ROLE}"',
                    f'REVOKE ALL ON DATABASE "{name}" FROM PUBLIC',
                    f'GRANT CONNECT ON DATABASE "{name}" TO "{APP_ROLE}"',
                ):
                    await conn.execute(text(stmt))
        finally:
            await engine.dispose()
        return TestDatabase(name=name, owner=self.settings(name, OWNER_ROLE), app=self.settings(name, APP_ROLE))

    async def new_migrated_database(self) -> TestDatabase:
        db = await self.new_database()
        engine = create_engine(db.owner)
        try:
            await migrate(engine, APP_ROLE)
        finally:
            await engine.dispose()
        return db
