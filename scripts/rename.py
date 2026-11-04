#!/usr/bin/env python3
"""Rename a fresh clone of the template: database and role names, Docker image, package names, GitHub repository and branding.

Usage: python3 scripts/rename.py <name> [--repo <owner/repo>]
Run from the repository root of an unrenamed clone. The repository defaults to Will413028/<name>. Before changing
anything the script checks that every tracked file naming the placeholder is in one of the lists below (or KEPT), so a
failed run leaves the clone untouched. Review the changes with `git diff`.
"""

import argparse
import pathlib
import re
import subprocess
import sys

OLD_NAME = "template-fastapi-nextjs"
OLD_DB = "template_fastapi_nextjs"
OLD_REPO = f"Will413028/{OLD_NAME}"
DEFAULT_REPO = "Will413028/{name}"
# Files that hold the database, role and image names (database names are unquoted identifiers: no hyphens).
STACK_FILES = (
    "docker-compose.yml",
    ".env-example",
    ".github/workflows/ci.yml",
    "backend/tests/dbtest.py",
    "docs/rules/database-design.md",
    "docs/privacy/data-inventory.md",
)
# Package names: <name>-backend (uv) and <name>-frontend (pnpm); uv.lock repeats the backend's.
PACKAGE_FILES = ("backend/pyproject.toml", "backend/uv.lock", "frontend/package.json")
# Links to the GitHub repository.
REPO_FILES = ("SECURITY.md", ".github/workflows/security.yml")
BRAND_FILES = (
    "frontend/src/app/layout.tsx",
    "frontend/messages/en.json",
    "frontend/messages/zh-TW.json",
    "frontend/README.md",
    "backend/api/openapi.yaml",
    "backend/src/app/platform/hibp.py",
    "AGENTS.md",
    "README.md",
)
# May keep naming the template: the plans record its history, and this script and its test name the placeholder.
KEPT = ("docs/plans/", "scripts/rename.py", "scripts/test_rename.py")
NAME_PATTERN = re.compile(r"[a-z](?:[a-z0-9-]*[a-z0-9])?")
# PostgreSQL limits identifiers to 63 bytes; the longest one built from the name is <db>_owner.
MAX_NAME = 63 - len("_owner")
# The database name is an unquoted identifier: PostgreSQL's reserved key words cannot be one
# (https://www.postgresql.org/docs/current/sql-keywords-appendix.html, column "PostgreSQL", reserved).
RESERVED = frozenset(
    "all analyse analyze and any array as asc asymmetric authorization binary both case cast check collate collation "
    "column concurrently constraint create cross current_catalog current_date current_role current_schema current_time "
    "current_timestamp current_user default deferrable desc distinct do else end except false fetch for foreign freeze "
    "from full grant group having ilike in initially inner intersect into is isnull join lateral leading left like "
    "limit localtime localtimestamp natural not notnull null offset on only or order outer overlaps placing primary "
    "references returning right select session_user similar some symmetric system_user table tablesample then to "
    "trailing true union unique user using variadic verbose when where window with".split()
)
REPO_PATTERN = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
PLACEHOLDER = re.compile(rf"{OLD_NAME}|{OLD_DB}")


LISTED = frozenset(STACK_FILES + PACKAGE_FILES + REPO_FILES + BRAND_FILES)


def update(path: pathlib.Path, change) -> None:
    text = path.read_text(encoding="utf-8")
    new = change(text)
    if new != text:
        path.write_text(new, encoding="utf-8")
        print(f"Updated {path}")


def leftovers(root: pathlib.Path) -> list[str]:
    files = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.split("\0")
    found = []
    for rel in files:
        path = root / rel
        if not rel or rel.startswith(KEPT) or rel in LISTED or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        found += [f"{rel}:{n}" for n, line in enumerate(text.splitlines(), 1) if PLACEHOLDER.search(line)]
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", help="lowercase letters, digits and hyphens, e.g. my-project")
    parser.add_argument("--repo", help=f"GitHub owner/repo (default {DEFAULT_REPO})")
    args = parser.parse_args()

    name = args.name
    if not NAME_PATTERN.fullmatch(name):
        parser.error(f"invalid name {name!r}: use lowercase letters, digits and hyphens, starting with a letter")
    repo = args.repo or DEFAULT_REPO.format(name=name)
    if not REPO_PATTERN.fullmatch(repo):
        parser.error(f"invalid repository {repo!r}: use owner/repo")
    db = name.replace("-", "_")
    if len(name) > MAX_NAME or db in RESERVED or OLD_NAME in name:
        parser.error(
            f"invalid name {name!r}: at most {MAX_NAME} characters, not a PostgreSQL reserved word, not containing {OLD_NAME}"
        )
    root = pathlib.Path.cwd()
    if not (root / "backend/pyproject.toml").exists():
        parser.error("backend/pyproject.toml not found; run from the repository root")
    if OLD_NAME not in (root / "backend/pyproject.toml").read_text(encoding="utf-8"):
        parser.error("this clone is already renamed")
    found = leftovers(root)
    if found:
        print(
            f"error: these lines name {OLD_NAME} or {OLD_DB} in files scripts/rename.py does not list; add the files"
            " to a list there and run again (nothing was changed):",
            file=sys.stderr,
        )
        print("\n".join(f"  {line}" for line in found), file=sys.stderr)
        return 1
    print(f"Renaming project to {name!r} (repository {repo})...")

    for rel in REPO_FILES:
        update(root / rel, lambda t: t.replace(OLD_REPO, repo))
    for rel in STACK_FILES:
        # the database and its roles (<db>_owner, <db>_app)
        update(root / rel, lambda t: t.replace(OLD_DB, db))
    for rel in STACK_FILES + PACKAGE_FILES + BRAND_FILES:
        update(root / rel, lambda t: t.replace(OLD_NAME, name))
    for rel in sorted(LISTED):
        if PLACEHOLDER.search((root / rel).read_text(encoding="utf-8")):
            print(f"error: {rel} still names the placeholder after the rename; fix scripts/rename.py", file=sys.stderr)
            return 1
    print("Done! Review the changes with: git diff")
    print("An existing .env keeps the old database and role names; compare it with .env-example.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
