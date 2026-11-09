"""Step 4b, one-off: every old validation behaviour B1-B68 (inventory.md section 1) has a row in b-coverage.md, every
test it names exists, and a row without a test says why it was deleted.

    uv run --no-project --python 3.14 python docs/plans/2026-11-08-fastapi-code-first/check_b_coverage.py [table]

Exit 1 lists every problem. The optional argument replaces b-coverage.md (the mutation check uses a copy).
"""

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
TESTS = HERE.parents[2] / "backend" / "tests"
COUNT = 68
ROW = re.compile(r"^\| (B\d+) \| (.*?) \| (.*?) \|$")
REF = re.compile(r"([\w]+\.py)::([\w]+)")


def problems(table: pathlib.Path) -> list[str]:
    found: list[str] = []
    rows: dict[str, tuple[str, str]] = {}
    for line in table.read_text(encoding="utf-8").splitlines():
        match = ROW.match(line)
        if match:
            label, tests, outcome = match.groups()
            if label in rows:
                found.append(f"{label}: two rows")
            rows[label] = (tests.strip(), outcome.strip())
    expected = {f"B{n}" for n in range(1, COUNT + 1)}
    found.extend(f"{label}: no row" for label in sorted(expected - rows.keys(), key=lambda b: int(b[1:])))
    found.extend(f"{label}: not in inventory.md" for label in sorted(rows.keys() - expected))
    for label, (tests, outcome) in rows.items():
        refs = REF.findall(tests)
        if not refs and not outcome.startswith("刪除："):
            found.append(f"{label}: no test and no deletion reason")
        if outcome in {"", "刪除：", "刪除"}:
            found.append(f"{label}: no outcome")
        for module, name in refs:
            path = TESTS / module
            if not path.is_file():
                found.append(f"{label}: {module} does not exist")
            elif not re.search(rf"^(async )?def {name}\(", path.read_text(encoding="utf-8"), re.MULTILINE):
                found.append(f"{label}: {module} has no {name}")
    return found


def main() -> int:
    table = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "b-coverage.md"
    found = problems(table)
    for line in found:
        print(line)
    print(f"{COUNT} behaviours, {len(found)} problems")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
