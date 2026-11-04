#!/usr/bin/env python3
"""Report where the files this template shares with template-go-nextjs have drifted apart.

Usage: python3 scripts/check_go_drift.py --go-repo <path to a template-go-nextjs clone> [--ref <commit>] [--diff]
The two templates share the frontend, the HTTP contract and a few repository files. Each shared file must equal the
Go version's at --ref (default dev) once the brand names are swapped (BRAND); a file present on only one side of a
shared directory is drift too. EXCEPTIONS lists the files that differ on purpose and why. Exit 0 when clean, 1 on
drift: port the change, or add the file to EXCEPTIONS with the reason. Run it at the weekly review (not in CI: a change
in the Go repository would turn this repository's CI red).
"""

import argparse
import difflib
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Directories whose every file is shared, except EXCEPTIONS.
SHARED_DIRS = ("frontend/",)
SHARED_FILES = (
    "backend/api/openapi.yaml",
    ".github/workflows/security.yml",
    ".github/workflows/tool-versions.yml",
    ".gitleaksignore",
    "LICENSE",
    "SECURITY.md",
)
# Go text -> this repository's text, applied to the Go version before comparing.
BRAND = {
    "template-go-nextjs": "template-fastapi-nextjs",
    "Next.js, Go, and": "Next.js, FastAPI, and",
    "Next.js、Go 和": "Next.js、FastAPI 和",
}
# Shared-directory files that differ on purpose: the deployment topology (Vercel rewrite instead of Caddy), decided in
# docs/adr/2026-11-05-vercel-rewrite-and-cloudflare-tunnel.md D1 and D3; "topology" below refers to it.
EXCEPTIONS = {
    "frontend/.env.example": "API_URL and ORIGIN_SECRET for server-side calls to the backend (topology)",
    "frontend/AGENTS.md": "describes the Vercel rewrite instead of Caddy (topology)",
    "frontend/next.config.ts": "development rewrite only; Vercel routes /api in production (topology)",
    "frontend/vercel.json": "the /api rewrite with the origin secret (topology); Go has no Vercel config",
    "frontend/src/lib/env.ts": "ORIGIN_SECRET for server-side calls (topology)",
    "frontend/src/lib/api/client.ts": "server-side calls carry the origin secret (topology)",
    "frontend/src/features/auth/__tests__/server.test.ts": "covers the origin secret header (topology)",
}


def git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def go_files(go_repo: pathlib.Path, ref: str) -> set[str]:
    return set(git(go_repo, "ls-tree", "-r", "--name-only", ref).splitlines())


def our_files() -> set[str]:
    # the working tree: tracked files not deleted, plus new files not yet added
    listed = git(ROOT, "ls-files", "--cached", "--others", "--exclude-standard").splitlines()
    return {p for p in listed if (ROOT / p).is_file()}


def shared(path: str) -> bool:
    return (path in SHARED_FILES or path.startswith(SHARED_DIRS)) and path not in EXCEPTIONS


def go_text(go_repo: pathlib.Path, ref: str, path: str) -> str:
    text = git(go_repo, "show", f"{ref}:{path}")
    for old, new in BRAND.items():
        text = text.replace(old, new)
    return text


def drift(go_repo: pathlib.Path, ref: str, show_diff: bool) -> list[str]:
    ours, theirs = our_files(), go_files(go_repo, ref)
    problems = []
    for path in sorted(p for p in ours | theirs if shared(p)):
        if path not in theirs:
            problems.append(f"only here: {path}")
        elif path not in ours:
            problems.append(f"only in Go: {path}")
        else:
            expected = go_text(go_repo, ref, path)
            actual = (ROOT / path).read_text(encoding="utf-8")
            if expected != actual:
                problems.append(f"differs: {path}")
                if show_diff:
                    problems += difflib.unified_diff(
                        expected.splitlines(), actual.splitlines(), f"go/{path}", path, lineterm=""
                    )
    for path in EXCEPTIONS:
        if path not in ours:
            problems.append(f"exception names a file that no longer exists here: {path}")
        elif path in theirs and go_text(go_repo, ref, path) == (ROOT / path).read_text(encoding="utf-8"):
            problems.append(f"exception no longer differs from Go; remove it: {path}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--go-repo", required=True, type=pathlib.Path, help="a clone of template-go-nextjs")
    parser.add_argument("--ref", default="dev", help="Go commit to compare with (default dev; fetch first)")
    parser.add_argument("--diff", action="store_true", help="print a unified diff for each differing file")
    args = parser.parse_args()
    commit = git(args.go_repo, "rev-parse", "--short", f"{args.ref}^{{commit}}").strip()
    problems = drift(args.go_repo, args.ref, args.diff)
    if problems:
        print(f"Drift from template-go-nextjs@{commit}:")
        print("\n".join(problems))
        return 1
    print(f"No drift from template-go-nextjs@{commit}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
