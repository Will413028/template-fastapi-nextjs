"""seed-admin's input checks, which need no database (Go version: cmd/app/seed_test.go
TestSeedAdminRejectsShortPassword). Creating the account is tested against PostgreSQL in test_seed_db.py."""

import pytest

from app.main.seed import SeedError, validate_admin_input

LONG_ENOUGH = "a long enough password"


@pytest.mark.parametrize(
    ("account", "password", "message"),
    [
        ("root", "fourteen chars", "ADMIN_PASSWORD: String should have at least 15 characters"),
        ("r!", LONG_ENOUGH, "ADMIN_ACCOUNT: "),
        ("root!", LONG_ENOUGH, "ADMIN_ACCOUNT: String should match pattern"),
        ("root", "p" * 129, "ADMIN_PASSWORD: String should have at most 128 characters"),
    ],
)
def test_the_input_follows_create_user_request_and_is_never_echoed(account: str, password: str, message: str) -> None:
    # there is no built-in default password: the contract's rules apply as for POST /api/users
    with pytest.raises(SeedError) as err:
        validate_admin_input(account, password)
    assert message in str(err.value)
    assert password not in str(err.value)


def test_valid_input_passes() -> None:
    validate_admin_input("Root", LONG_ENOUGH)
