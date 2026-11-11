from typing import TYPE_CHECKING

import pytest

from dbtest import Server

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(scope="session")
def postgres() -> Iterator[Server]:
    server = Server.start()
    yield server
    server.stop()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    # A test that starts PostgreSQL must carry the db marker, so -m "not db" really leaves every container out.
    for item in items:
        if "postgres" in getattr(item, "fixturenames", ()) and item.get_closest_marker("db") is None:
            msg = f"{item.nodeid} uses the postgres fixture without @pytest.mark.db"
            raise pytest.UsageError(msg)
