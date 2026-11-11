"""The `app` command.

Each subcommand is a module with `run(environ) -> int`, imported only when that subcommand runs, so `app healthcheck`
never loads the web framework or the database driver (the healthcheck contract in .importlinter checks this). Each
command reads only the settings it needs from the environment. Add a command by adding a module and a line to COMMANDS.
"""

import argparse
import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

COMMANDS = {
    "serve": "app.cli.server",
    "migrate": "app.cli.migrate",
    "healthcheck": "app.cli.healthcheck",
    "seed-admin": "app.cli.seed",
    "recover-admin": "app.cli.recover",
    "openapi": "app.cli.openapi",
}


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app")
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args(argv)
    # environ None means the process environment, which only app.core.config reads
    command: Callable[[Mapping[str, str] | None], int] = importlib.import_module(COMMANDS[args.command]).run
    return command(environ)
