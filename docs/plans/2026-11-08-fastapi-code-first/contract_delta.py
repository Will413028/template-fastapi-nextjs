"""Step 4a acceptance: every difference oasdiff finds between the hand-written backend/api/openapi.yaml and the
generated backend/api/openapi.json maps to a destination in inventory.md section 2 (an F row) or to a row this script
names (T1, T2). An unmapped difference fails (exit 1). One-off: it is deleted with the yaml in step 4b.

Each leaf of `oasdiff diff -f json` (a {from, to} pair, or one added or deleted list entry) goes through RULES in
order; the first rule whose path pattern matches classifies it. A description counts as unchanged when only its line
breaks and trailing newline differ (YAML block scalars against Python strings); any other description change fails.
`oasdiff changelog --level INFO` ids are mapped the same way, so a change oasdiff rates as a rule (breaking or not)
is accounted for too.

Run from the repository root, after make -C backend tools and make -C backend gen:
    python3 -I docs/plans/2026-11-08-fastapi-code-first/contract_delta.py backend/bin/oasdiff-1.35.0 \
        [<new spec> [<old spec>]]
The optional arguments replace backend/api/openapi.json and backend/api/openapi.yaml (the script's own mutation
checks).
"""

import collections
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

OLD = Path("backend/api/openapi.yaml")
NEW = Path("backend/api/openapi.json")

_PROBLEM_PROPERTIES = {"type", "title", "status", "detail", "code", "request_id"}
_ADDED = object()  # a {from, to} pair whose `from` is empty: something new, not something changed


# (destination, path pattern, accepted values). Paths are the oasdiff JSON keys joined with "/". A leaf matches a rule
# only when its path matches and its value is accepted (None accepts any); a leaf no rule accepts is unmapped.
RULES: list[tuple[str, str, object]] = [
    (
        "T1 Pydantic writes a title on every schema and property (documentation only)",
        r"^(paths|components)/.*/title$",
        _ADDED,
    ),
    (
        "F30 example -> examples (JSON Schema 2020-12)",
        r"/(type|pointer|code)/examples?$",
        None,
    ),
    ("F1 openapi 3.0.3 -> 3.1.0", r"^openAPI$", {"from": "3.0.3", "to": "3.1.0"}),
    (
        "F2 servers /api removed, paths carry /api (D4)",
        r"^servers/deleted/\d+$",
        {"/api"},
    ),
    (
        "F3 global security -> per operation, from the access markers",
        r"^(security/deleted/\d+(/index)?|paths/.*/securityRequirements/(list(Added|Deleted)|added/\d+/index))$",
        {"sessionCookie", True, 0},
    ),
    ("F20 ItemTitle inlined", r"^components/schemas/deleted/\d+$", {"ItemTitle"}),
    (
        "T2 reusable parameters, headers and responses inlined per operation (F8, F10-F15)",
        r"^components/(parameters|headers|responses)/deleted/\d+$",
        {
            "Id",
            "Limit",
            "Cursor",
            "Location",
            "SetSessionCookie",
            "ClearSessionCookie",
            "RetryAfter",
            "BadRequest",
            "Unauthenticated",
            "Forbidden",
            "NotFound",
            "Conflict",
            "PayloadTooLarge",
            "UnsupportedMediaType",
            "ValidationFailed",
            "RateLimited",
            "InternalError",
        },
    ),
    (
        "F15 the id path parameter moves from the path item to each operation",
        r"/parameters/(added|deleted)/path/\d+$",
        {"id"},
    ),
    (
        "F14 the cursor query parameter becomes nullable (cursor: str | None)",
        r"/parameters/modified/query/cursor/schema/nullable$",
        {json.dumps({"from": False, "to": True})},
    ),
    (
        "F32 ValidationProblem allOf [Problem, {errors}] -> one flat object (contents: check_flat_validation_problem)",
        r"(/422/content/modified/application/problem\+json/schema|^components/schemas/modified/ValidationProblem)/"
        r"(allOf/deleted/\d+/(index|component)|properties/added/\d+|required/added/\d+|type/added/\d+)$",
        {"Problem", 0, 1, "object", "errors", *_PROBLEM_PROPERTIES},
    ),
    (
        "F26 SignInLock.failures format int64 dropped",
        r"/failures/format$",
        {json.dumps({"from": "int64", "to": ""})},
    ),
]

# changelog id -> (destination, a pattern its text must match)
CHANGELOG: dict[str, tuple[str, str]] = {
    "api-global-security-removed": ("F3", r"`sessionCookie`"),
    "api-schema-removed": ("F20", r"`ItemTitle`$"),
    "api-version-not-bumped": (
        "F3 F14 F15 F26 (oasdiff counts them as breaking; the version stays 0.1.0)",
        "",
    ),
    "new-request-path-parameter": ("F15", r"parameter `id`$"),
    "request-parameter-became-nullable": (
        "F14",
        r"parameter `cursor` became nullable$",
    ),
    "response-body-all-of-removed": ("F32", r"status `422`$"),
    "response-body-type-specialized": (
        "F32",
        r"from `any` to `object` for status `422`$",
    ),
    "response-optional-property-added": (
        "F32",
        r"`(detail|code|request_id)` to the response with the `422` status$",
    ),
    "response-required-property-added": (
        "F32",
        r"`(type|title|status|errors)` to the response with the `422` status$",
    ),
    "response-property-type-changed": (
        "F26",
        r"`failures` response's property `format` changed from `int64`",
    ),
}


def accepted(value: object, allowed: object) -> bool:
    if allowed is None:
        return True
    if allowed is _ADDED:
        return isinstance(value, dict) and not value.get("from") and bool(value.get("to"))
    key = json.dumps(value) if isinstance(value, dict) else value
    return isinstance(allowed, set) and key in allowed or allowed == value


def check_flat_validation_problem(new: Path) -> list[str]:
    """oasdiff reports the flattened ValidationProblem's properties as added and does not compare their contents, so
    they are checked here: Problem's properties plus errors, an array of FieldError, required Problem's plus errors."""
    schemas = json.loads(new.read_text(encoding="utf-8"))["components"]["schemas"]
    problem, flat = schemas["Problem"], schemas["ValidationProblem"]
    found = []
    expected = {
        **problem["properties"],
        "errors": {
            "items": {"$ref": "#/components/schemas/FieldError"},
            "type": "array",
            "title": "Errors",
        },
    }
    if flat["properties"] != expected:
        found.append(f"ValidationProblem.properties differ from Problem's plus errors: {flat['properties']}")
    if sorted(flat["required"]) != sorted([*problem["required"], "errors"]):
        found.append(f"ValidationProblem.required is {flat['required']}")
    return found


def as_nullable(node: object) -> tuple[object, int]:
    """The document with every `anyOf [X, {type: null}]` written as 3.0's X plus `nullable: true` (F14, F21, F22), and
    how many it rewrote. oasdiff does not compare the branches of an anyOf the old document lacks, so without this a
    changed X (a new maxLength, another type) would pass as part of the nullable rewrite."""
    if isinstance(node, list):
        rewritten = [as_nullable(item) for item in node]
        return [item for item, _ in rewritten], sum(count for _, count in rewritten)
    if not isinstance(node, dict):
        return node, 0
    count = 0
    result: dict[str, object] = {}
    for key, value in node.items():
        result[key], inner = as_nullable(value)
        count += inner
    branches = result.get("anyOf")
    if isinstance(branches, list) and len(branches) == 2 and {"type": "null"} in branches:
        (other,) = [branch for branch in branches if branch != {"type": "null"}]
        del result["anyOf"]
        if isinstance(other, dict) and "$ref" not in other:
            result = {**other, **result, "nullable": True}
            count += 1
        else:
            result["anyOf"] = branches  # a nullable reference has no 3.0 form here; left for the rules
    return result, count


def oasdiff(binary: str, old: Path, new: Path, *args: str) -> object:
    out = subprocess.run(
        [binary, *args, str(old), str(new), "--prefix-base", "/api", "-f", "json"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out) if out.strip() else []


def leaves(node: object, path: tuple[str, ...] = ()) -> list[tuple[str, object]]:
    if isinstance(node, dict):
        if node and set(node) <= {"from", "to"}:
            return [("/".join(path), node)]
        return [leaf for key, value in node.items() for leaf in leaves(value, (*path, str(key)))]
    if isinstance(node, list):
        return [leaf for i, value in enumerate(node) for leaf in leaves(value, (*path, str(i)))]
    return [("/".join(path), node)]


def same_text(change: object) -> bool:
    if not isinstance(change, dict):
        return False
    old, new = change.get("from") or "", change.get("to") or ""
    return " ".join(str(old).split()) == " ".join(str(new).split())


def main() -> int:
    binary, *rest = sys.argv[1:]
    generated = Path(rest[0]) if rest else NEW
    old = Path(rest[1]) if len(rest) > 1 else OLD
    counts: collections.Counter[str] = collections.Counter()
    normalized, nullable = as_nullable(json.loads(generated.read_text(encoding="utf-8")))
    counts["F14 F21 F22 anyOf [type, null] compared as type + nullable: true (as_nullable)"] = nullable
    with tempfile.TemporaryDirectory() as tmp:
        new = Path(tmp) / "openapi.json"
        new.write_text(json.dumps(normalized), encoding="utf-8")
        return report(binary, old, new, generated, counts)


def report(binary: str, old: Path, new: Path, generated: Path, counts: collections.Counter[str]) -> int:
    unmapped: list[str] = []
    for path, value in leaves(oasdiff(binary, old, new, "diff")):
        if path.endswith("/description") or path == "info/description":
            if same_text(value):
                counts["(description: line breaks only)"] += 1
            elif isinstance(value, dict) and not value.get("from") and re.search(r"/(limit|cursor)/schema/", path):
                counts["F13 F14 FastAPI copies the parameter's description into its schema"] += 1
            else:
                unmapped.append(f"{path} = {value}")
            continue
        destination = next(
            (name for name, pattern, allowed in RULES if re.search(pattern, path) and accepted(value, allowed)),
            None,
        )
        if destination is None:
            unmapped.append(f"{path} = {value}")
        else:
            counts[destination] += 1
    changelog = oasdiff(binary, old, new, "changelog", "--level", "INFO")
    assert isinstance(changelog, list)
    ids: collections.Counter[str] = collections.Counter()
    for entry in changelog:
        change_id = entry["id"]
        if change_id in CHANGELOG and re.search(CHANGELOG[change_id][1], entry["text"]):
            ids[f"{change_id} -> {CHANGELOG[change_id][0]}"] += 1
        else:
            unmapped.append(f"changelog {change_id}: {entry['text']}")
    unmapped.extend(check_flat_validation_problem(generated))
    print("diff leaves by destination:")
    for name, count in sorted(counts.items()):
        print(f"  {count:4d}  {name}")
    print("changelog entries by id:")
    for name, count in sorted(ids.items()):
        print(f"  {count:4d}  {name}")
    if unmapped:
        print(f"UNMAPPED ({len(unmapped)}):")
        for line in unmapped:
            print(f"  {line}")
        return 1
    print("every difference is mapped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
