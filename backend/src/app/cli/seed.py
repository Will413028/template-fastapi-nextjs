"""`app seed-admin`: creates the admin account unless it exists; it is the only way to create one (Go version:
cmd/app/seed.go). The root Makefile's `make seed ADMIN_ACCOUNT=<name>` runs it in the stack with the password on
stdin.

The input is checked against CreateUserRequest (app.modules.auth.schemas), the model POST /api/users validates its
body with and the contract is generated from, and the password against the common-password list. Messages name the
variable and the rule, never the value. An existing account is left exactly as it is: seed-admin never promotes or
changes a user.

`app recover-admin` (app.cli.recover) shares the input checks and the AuthService assembly.
"""

import asyncio
import sys
from typing import TYPE_CHECKING

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import ConfigError, SeedSettings
from app.core.db import Database
from app.core.errors import AppError, Kind
from app.core.hibp import HIBPClient
from app.modules.auth.schemas import CreateUserRequest
from app.modules.auth.service import AuthService, NoSuchAdminError, account_key

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

# The environment variable each CreateUserRequest property comes from, for messages.
_INPUT_VARS = {"account": "ADMIN_ACCOUNT", "password": "ADMIN_PASSWORD"}


class SeedError(Exception):
    """Input seed-admin refuses; the message never contains the input."""


def validate_admin_input(account: str, password: str) -> None:
    try:
        CreateUserRequest.model_validate({"account": account, "password": password})
    except ValidationError as err:
        problems = sorted(
            f"{_INPUT_VARS.get(str(e['loc'][0]) if e['loc'] else '', 'input')}: {e['msg']}"
            for e in err.errors(include_input=False, include_url=False, include_context=False)
        )
        raise SeedError("invalid admin account: " + "; ".join(problems)) from None


async def seed_admin(service: AuthService, account: str, password: str) -> str:
    """Creates the admin and returns what to print; SeedError for input the rules refuse."""
    validate_admin_input(account, password)
    try:
        created = await service.ensure_admin(account, password)
    except AppError as err:
        if err.kind is not Kind.VALIDATION:
            raise
        msg = f"ADMIN_PASSWORD: {err.detail} ({err.code})"
        raise SeedError(msg) from None
    name = account_key(account)
    if created:
        return f"admin account {name!r} created"
    return f"account {name!r} already exists; nothing changed"


async def recover_admin(service: AuthService, account: str, password: str) -> str:
    """Gives an existing admin a new password, enables it and signs it out everywhere (AuthService.recover_admin): the
    way back when every admin is disabled, locked out or has lost the password. The input is checked like
    seed_admin's; SeedError for input the rules refuse or an account that is not an admin."""
    validate_admin_input(account, password)
    name = account_key(account)
    try:
        await service.recover_admin(account, password)
    except AppError as err:
        if err.kind is not Kind.VALIDATION:
            raise
        msg = f"ADMIN_PASSWORD: {err.detail} ({err.code})"
        raise SeedError(msg) from None
    except NoSuchAdminError:
        msg = f"no admin account {name!r}: recover-admin changes only an existing admin"
        raise SeedError(msg) from None
    return f"admin account {name!r} recovered: new password set, enabled, signed out everywhere"


type AdminCommand = Callable[[AuthService, str, str], Awaitable[str]]


async def _run(settings: SeedSettings, command: AdminCommand) -> str:
    database = Database.open(settings.database)
    # the session limits stay at their defaults: the admin commands issue no session
    service = AuthService(
        database.engine,
        breach=HIBPClient() if settings.password_hibp_check else None,
    )
    try:
        return await command(service, settings.admin_account, settings.admin_password)
    finally:
        service.close()
        await database.close()


def run_admin_command(name: str, command: AdminCommand, environ: Mapping[str, str] | None) -> int:
    """Runs seed-admin or recover-admin with ADMIN_ACCOUNT and ADMIN_PASSWORD, printing the outcome under name."""
    try:
        settings = SeedSettings.from_env(environ)
    except ConfigError as err:
        sys.stderr.write(f"{name}: {err}\n")
        return 1
    try:
        message = asyncio.run(_run(settings, command))
    except (SeedError, SQLAlchemyError, OSError) as err:
        # SQLAlchemy prints URLs with the password masked, and asyncpg's errors carry no password
        sys.stderr.write(f"{name}: {err}\n")
        return 1
    sys.stdout.write(f"{name}: {message}\n")
    return 0


def run(environ: Mapping[str, str] | None) -> int:
    return run_admin_command("seed-admin", seed_admin, environ)
