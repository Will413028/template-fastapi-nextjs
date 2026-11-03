"""The `app` command.

Each subcommand is a module with `run(settings) -> int`, imported only when that subcommand runs, so `app healthcheck`
never loads the web framework (the healthcheck contract in .importlinter checks this). Add a command by adding a module
and a line to COMMANDS.
"""

import argparse
import importlib
import sys
from typing import TYPE_CHECKING

from app.platform.config import Settings

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

COMMANDS = {
    "serve": "app.main.server",
    "healthcheck": "app.main.healthcheck",
}


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app")
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args(argv)
    command: Callable[[Settings], int] = importlib.import_module(COMMANDS[args.command]).run
    return command(Settings.from_env(environ))


if __name__ == "__main__":
    sys.exit(main())
