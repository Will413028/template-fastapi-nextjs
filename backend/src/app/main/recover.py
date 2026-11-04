"""`app recover-admin`: gives an existing admin a new password, enables it and signs it out everywhere (Go version:
cmd/app/seed.go recoverAdmin). The root Makefile's `make recover-admin ADMIN_ACCOUNT=<name>` runs it in the stack with
the password on stdin. Input checks and assembly: app.main.seed.
"""

from typing import TYPE_CHECKING

from app.main.seed import recover_admin, run_admin_command

if TYPE_CHECKING:
    from collections.abc import Mapping


def run(environ: Mapping[str, str] | None) -> int:
    return run_admin_command("recover-admin", recover_admin, environ)
