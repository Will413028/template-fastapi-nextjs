"""seed-admin and recover-admin against PostgreSQL (Go version: cmd/app/seed_test.go). CI only (make test-db); the
input checks that need no database are in test_seed.py."""

from typing import TYPE_CHECKING

import pytest

from app.main.seed import SeedError, recover_admin, seed_admin
from app.modules.auth.repository import UNTRUSTED
from authtest import LONG_ENOUGH, AuthAPI, auth_api

if TYPE_CHECKING:
    from collections.abc import Generator

    from dbtest import Server

pytestmark = pytest.mark.db

ANOTHER = "another long enough phrase"


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


def test_recover_admin_gives_an_admin_a_new_password_and_a_clean_slate(api: AuthAPI) -> None:
    # Go version: seed_test.go TestRecoverAdmin; also its sessions and trusted devices go
    api.call(seed_admin, api.service, "root", LONG_ENOUGH)
    api.must_sign_in("root")  # a session and a trusted device
    api.sql("UPDATE users SET is_disabled = true WHERE account = 'root'")
    for client in (UNTRUSTED, "ip:192.0.2.9"):
        api.sql(
            "INSERT INTO sign_in_throttles (account_key, client_key, failures, next_allowed_at, updated_at)"
            " VALUES ('root', :client, 200, now() + interval '1 hour', now())",
            client=client,
        )
    message = api.call(recover_admin, api.service, "Root", ANOTHER)

    assert message == "admin account 'root' recovered: new password set, enabled, signed out everywhere"
    assert api.sql("SELECT count(*) FROM sessions") == [(0,)]
    assert api.sql("SELECT count(*) FROM trusted_devices") == [(0,)]
    assert api.sql("SELECT count(*) FROM sign_in_throttles") == [(0,)]
    assert api.sql("SELECT action, actor_id IS NULL AS by_operator FROM audit_events") == [("admin.recovered", True)]
    api.must_sign_in("root", ANOTHER)


def test_recover_admin_changes_only_an_existing_admin(api: AuthAPI) -> None:
    api.must_sign_up("alice")
    before = api.sql("SELECT account, role, password_hash, is_disabled FROM users")
    for account in ("alice", "nobody"):
        with pytest.raises(SeedError, match=f"no admin account '{account}'"):
            api.call(recover_admin, api.service, account, ANOTHER)
    assert api.sql("SELECT account, role, password_hash, is_disabled FROM users") == before  # never creates one
    assert api.sql("SELECT count(*) FROM audit_events") == [(0,)]
    # the account is checked first: a mistyped one is reported as such even with a password the list refuses
    with pytest.raises(SeedError, match="no admin account 'nobody'"):
        api.call(recover_admin, api.service, "nobody", "demon1q2w3e4r5t")


def test_recover_admin_refuses_a_common_password(api: AuthAPI) -> None:
    api.call(seed_admin, api.service, "root", LONG_ENOUGH)
    with pytest.raises(SeedError, match="password_too_common"):
        api.call(recover_admin, api.service, "root", "demon1q2w3e4r5t")
    api.must_sign_in("root")
