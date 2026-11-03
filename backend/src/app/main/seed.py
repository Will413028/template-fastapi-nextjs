"""`app seed-admin`: creates the admin account unless it exists; it is the only way to create one (Go version:
cmd/app/seed.go). The root Makefile's `make seed ADMIN_ACCOUNT=<name>` runs it in the stack with the password on
stdin.

The input is checked against the contract's CreateUserRequest, the rules POST /api/users applies, through the model
generated from it (app.web.contract_models, which gen-check keeps in step with the contract), and the password against
the common-password list. Messages name the variable and the rule, never the value. An existing account is left
exactly as it is: seed-admin never promotes or changes a user.
"""

import asyncio
import sys
from typing import TYPE_CHECKING

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.modules.auth.service import AuthService, account_key
from app.platform.config import DEFAULT_SESSION_ABSOLUTE, DEFAULT_SESSION_IDLE, ConfigError, SeedSettings
from app.platform.db import Database
from app.platform.errors import AppError, Kind
from app.web.contract_models import CreateUserRequest

if TYPE_CHECKING:
    from collections.abc import Mapping

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


async def _run(settings: SeedSettings) -> str:
    database = Database.open(settings.database)
    # the session limits do not matter here: seed-admin issues no session
    service = AuthService(database.engine, session_absolute=DEFAULT_SESSION_ABSOLUTE, session_idle=DEFAULT_SESSION_IDLE)
    try:
        return await seed_admin(service, settings.admin_account, settings.admin_password)
    finally:
        service.close()
        await database.close()


def run(environ: Mapping[str, str] | None) -> int:
    try:
        settings = SeedSettings.from_env(environ)
    except ConfigError as err:
        sys.stderr.write(f"seed-admin: {err}\n")
        return 1
    try:
        message = asyncio.run(_run(settings))
    except (SeedError, SQLAlchemyError, OSError) as err:
        # SQLAlchemy prints URLs with the password masked, and asyncpg's errors carry no password
        sys.stderr.write(f"seed-admin: {err}\n")
        return 1
    sys.stdout.write(f"seed-admin: {message}\n")
    return 0
