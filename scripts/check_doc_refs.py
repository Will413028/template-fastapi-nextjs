#!/usr/bin/env python3
"""Check that every reference to a document's decision or step names that document, which exists and defines it.

Usage: python3 scripts/check_doc_refs.py [root]   (default: the current directory)

Decisions are labelled D1, D2 … and procedures step 1, step 2 … inside the document that makes them, and those
numbers restart in every document. A bare label in a comment therefore points nowhere, or at the wrong decision once
another document reuses the number. So a label is written with its document:

    docs/adr/<file>.md D2, D3        a path, from the repository root (D1–D5 is a range: both ends must exist)
    ADR <slug> D2                    docs/adr/<date>-<slug>.md
    [text](<file>.md) D2             a Markdown link, relative to the file that holds it
    <date>-<slug>.md D2              an ADR's file name: next to the file that holds it, or in docs/adr/

and the document must exist and define the label (a heading, list item or table row that starts with it, bold or
not). A document may use its own labels bare. Every docs/ path ending in .md must exist too.

Checks the files git tracks (every file when root is not a repository), skipping binary files and the path prefixes
listed in .doc-refs-ignore, one per line (# starts a comment).
"""

import functools
import pathlib
import re
import subprocess
import sys

IGNORE_FILE = ".doc-refs-ignore"
_D = r"D\d{1,2}(?![\w-])"
_STEP = r"(?:[Ss]teps? |步驟 ?)\d+[a-z]?(?![\w-])"
LABEL = re.compile(rf"(?<![\w-])(?:{_D}|{_STEP})")
_LABELS = rf"(?:{_D}|{_STEP})(?:\s*(?:,|、|–|and|與|和)\s*(?:{_D}|{_STEP}))*"
REFERENCE = re.compile(
    r"(?:(?<![\w/.-])(?P<path>(?:docs|\.claude)/[\w./-]*\.md)"
    r"|\]\((?P<link>[^)\s]+\.md)\)"
    r"|(?<![\w/.-])(?P<dated>\d{4}-\d{2}-\d{2}-[\w.-]+\.md)"
    r"|(?<![\w-])ADR\s+(?P<slug>[a-z0-9][a-z0-9-]*)(?=,?\s+(?:D\d|[Ss]tep |步驟)))"
    rf"(?:`?,?\s+(?P<labels>{_LABELS}))?"
)


def _files(root: pathlib.Path) -> list[str]:
    if (root / ".git").exists():
        listed = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout
        return [name for name in listed.decode().split("\0") if name]
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and ".git" not in p.parts)


def _ignored(root: pathlib.Path) -> tuple[str, ...]:
    path = root / IGNORE_FILE
    if not path.is_file():
        return ()
    lines = (line.split("#", 1)[0].strip() for line in path.read_text(encoding="utf-8").splitlines())
    return tuple(line for line in lines if line)


def _normal(label: str) -> str:
    """One spelling per label: D2, step 6b."""
    return re.sub(r"^(?:[Ss]teps? |步驟 ?)", "step ", label)


@functools.cache
def _defined(path: pathlib.Path) -> frozenset[str]:
    """The labels a document defines: the first label of a heading, list item or table row."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return frozenset()
    start = re.compile(rf"^\s*(?:#+|[-*]|\d+\.|\|)?\s*\**\s*({_D}|{_STEP})", re.MULTILINE)
    return frozenset(_normal(m.group(1)) for m in start.finditer(text))


def _target(root: pathlib.Path, rel: str, match: re.Match[str]) -> tuple[pathlib.Path | None, str]:
    """The document a reference names, and how to call it in a message; None when it names no file."""
    if match["path"]:
        return root / match["path"], match["path"]
    if match["link"]:
        target = (root / rel).parent / match["link"]
        return target, match["link"]
    if match["dated"]:
        beside = (root / rel).parent / match["dated"]
        return (beside if beside.is_file() else root / "docs/adr" / match["dated"]), match["dated"]
    slug = match["slug"]
    found = sorted((root / "docs/adr").glob(f"[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-{slug}.md"))
    return (found[0] if len(found) == 1 else None), f"ADR {slug}"


# A comment leader at the start of a line, blanked so that a reference may wrap onto the next comment line
LEADER = re.compile(r"^[ \t]*(?:#+|//+|/?\*+|--|;+)", re.MULTILINE)


def check_text(root: pathlib.Path, rel: str, text: str) -> list[str]:
    problems: list[str] = []
    own = _defined(root / rel) if rel.endswith(".md") else frozenset()
    flat = LEADER.sub(lambda m: " " * len(m[0]), text)

    def at(offset: int) -> int:
        return text.count("\n", 0, offset) + 1

    covered: list[tuple[int, int]] = []
    for match in REFERENCE.finditer(flat):
        target, name = _target(root, rel, match)
        where = f"{rel}:{at(match.start())}"
        if match["labels"]:
            covered.append(match.span("labels"))
        if target is None:
            problems.append(f"{where}: {name} names no file docs/adr/<date>-{match['slug']}.md")
        elif not target.is_file():
            problems.append(f"{where}: {name} does not exist")
        elif match["labels"]:
            defined = _defined(target.resolve())
            problems += [
                f"{where}: {name} does not define {label}"
                for label in LABEL.findall(match["labels"])
                if _normal(label) not in defined
            ]
    for label in LABEL.finditer(flat):
        if any(start <= label.start() < end for start, end in covered) or _normal(label[0]) in own:
            continue
        problems.append(
            f"{rel}:{at(label.start())}: {label[0]!r} names no document "
            f"(write docs/<path>.md {label[0]} or ADR <slug> {label[0]})"
        )
    return sorted(problems, key=lambda p: int(p.split(":")[1]))


def check(root: pathlib.Path) -> list[str]:
    _defined.cache_clear()
    ignored = _ignored(root)
    problems: list[str] = []
    for rel in _files(root):
        if rel.startswith(ignored) or not (root / rel).is_file():
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        problems += check_text(root, rel, text)
    return problems


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    problems = check(root)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
