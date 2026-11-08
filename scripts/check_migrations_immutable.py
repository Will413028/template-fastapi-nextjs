#!/usr/bin/env python3
"""Revisions that exist on the base must not be modified, deleted or renamed (docs/rules/database-design.md).

Usage: uv run --no-project --python 3.14 python scripts/check_migrations_immutable.py [<base-ref>]   (default origin/main)

Compares every revision file in the merge-base of HEAD and the base with the working tree, so uncommitted changes are
covered too. A revision that is only on this branch may still change: it has not reached a database that matters.
"""

import os
import pathlib
import subprocess
import sys

VERSIONS = "backend/src/app/migrations/versions/"
# A git hook exports GIT_DIR; without a work tree git would then take the current directory as the repository root.
# Find the repository from this script's directory instead.
ENV = {k: v for k, v in os.environ.items() if k not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"}}


def git_bytes(*args: str, cwd: pathlib.Path) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True, cwd=cwd, env=ENV).stdout


def git(*args: str, cwd: pathlib.Path) -> str:
    return git_bytes(*args, cwd=cwd).decode()


def changed_revisions(base: str, root: pathlib.Path) -> list[str]:
    merge_base = git("merge-base", "HEAD", base, cwd=root).strip()
    listed = git("ls-tree", "-r", "--name-only", merge_base, "--", VERSIONS, cwd=root).split()
    changed = []
    for name in listed:
        if not name.endswith(".py"):
            continue
        current = root / name
        if not current.is_file() or current.read_bytes() != git_bytes("show", f"{merge_base}:{name}", cwd=root):
            changed.append(name)
    return changed


def main(argv: list[str]) -> int:
    base = argv[0] if argv else "origin/main"
    root = pathlib.Path(git("rev-parse", "--show-toplevel", cwd=pathlib.Path(__file__).parent).strip())
    changed = changed_revisions(base, root)
    if not changed:
        print("no existing revision was modified")
        return 0
    print("\n".join(changed))
    print("revisions on the base must not be modified, deleted or renamed; add a new revision", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
