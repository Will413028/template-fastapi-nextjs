"""seed-admin against PostgreSQL (Go version: cmd/app/seed_test.go). CI only (make test-db); the input checks that
need no database are in test_seed.py."""

from typing import TYPE_CHECKING

import pytest

from app.main.seed import SeedError, seed_admin
from authtest import LONG_ENOUGH, AuthAPI, auth_api

if TYPE_CHECKING:
    from collections.abc import Generator

    from dbtest import Server

pytestmark = pytest.mark.db


@pytest.fixture
def api(postgres: Server) -> Generator[AuthAPI]:
    with auth_api(postgres) as created:
        yield created


def test_seed_admin_creates_an_admin_who_can_sign_in(api: AuthAPI) -> None:
    assert api.call(seed_admin, api.service, "Root", LONG_ENOUGH) == "admin account 'root' created"
    assert api.sql("SELECT role FROM users WHERE account = 'root'") == [("admin",)]
    api.must_sign_in("root")


def test_seed_admin_refuses_a_common_password_and_creates_nothing(api: AuthAPI) -> None:
    common = "demon1q2w3e4r5t"
    with pytest.raises(SeedError) as err:
        api.call(seed_admin, api.service, "root", common)
    assert "ADMIN_PASSWORD" in str(err.value)
    assert "password_too_common" in str(err.value)
    assert common not in str(err.value)
    assert api.sql("SELECT count(*) FROM users") == [(0,)]


def test_seed_admin_leaves_an_existing_account_unchanged(api: AuthAPI) -> None:
    api.must_sign_up("alice")
    before = api.sql("SELECT role, password_hash FROM users")
    message = api.call(seed_admin, api.service, "ALICE", LONG_ENOUGH)
    assert message == "account 'alice' already exists; nothing changed"
    after = api.sql("SELECT role, password_hash FROM users")
    assert after == before
    assert after[0].role == "user"
    assert after[0].password_hash.startswith("$argon2id")
    # re-running with any password, even one the list refuses, is still a no-op
    assert api.call(seed_admin, api.service, "alice", "demon1q2w3e4r5t") == message
