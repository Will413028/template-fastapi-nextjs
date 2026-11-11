"""The contract generated from the routes (app.main.build_openapi): problems are application/problem+json, FastAPI's
own validation schema stays out, and each operation's security follows its access marker. make gen-check keeps the
committed api/openapi.json equal to it."""

import functools
from http import HTTPStatus
from typing import Any, cast

from app.main import build_openapi, problem_media_type

_METHODS = frozenset({"get", "put", "post", "delete", "patch"})


@functools.cache
def spec() -> dict[str, Any]:
    return build_openapi()


def operations() -> dict[str, dict[str, Any]]:
    """Each operation by its operationId."""
    return {
        operation["operationId"]: operation
        for item in spec()["paths"].values()
        for method, operation in item.items()
        if method in _METHODS
    }


def test_every_operation_is_generated() -> None:
    # each with an explicit camelCase operationId instead of FastAPI's generated create_item_api_items_post
    assert operations()
    assert all(name[0].islower() and "_" not in name for name in operations())


def test_problem_responses_are_only_problem_json() -> None:
    for name, operation in operations().items():
        for status, response in operation["responses"].items():
            if int(status) >= HTTPStatus.BAD_REQUEST:
                assert list(response["content"]) == ["application/problem+json"], (name, status)


def test_fastapi_validation_schema_is_left_out() -> None:
    # FastAPI adds HTTPValidationError to an operation with input that declares no 422
    schemas = spec()["components"]["schemas"]
    assert "HTTPValidationError" not in schemas
    assert "ValidationError" not in schemas
    for name, operation in operations().items():
        if operation.get("parameters") or operation.get("requestBody"):
            schema = operation["responses"]["422"]["content"]["application/problem+json"]["schema"]
            assert schema == {"$ref": "#/components/schemas/ValidationProblem"}, name


def test_security_follows_the_access_markers() -> None:
    public = {name for name, operation in operations().items() if "security" not in operation}
    assert public == {"createUser", "createSession", "deleteCurrentSession"}
    for name, operation in operations().items():
        if name not in public:
            assert operation["security"] == [{"sessionCookie": []}], name
    scheme = spec()["components"]["securitySchemes"]["sessionCookie"]
    assert (scheme["type"], scheme["in"], scheme["name"]) == ("apiKey", "cookie", "__Host-session")


def test_a_problem_schema_moves_from_any_success_media_type() -> None:
    # FastAPI writes the problem's schema under the route's success media type, application/json or a response_class's
    schema = {"$ref": "#/components/schemas/Problem"}
    for success in ("application/json", "text/html"):
        response = {"description": "x", "content": {success: {"schema": schema}, "application/problem+json": {}}}
        spec = {"paths": {"/api/x": {"get": {"responses": {"404": response}}}}}
        moved = problem_media_type(spec)["paths"]["/api/x"]["get"]["responses"]["404"]["content"]
        assert moved == {"application/problem+json": {"schema": schema}}, success


def test_a_patch_accepts_only_merge_patch() -> None:
    for name in ("patchUser", "updateItem"):
        assert list(operations()[name]["requestBody"]["content"]) == ["application/merge-patch+json"], name


def _patterns(node: object) -> list[str]:
    if isinstance(node, dict):
        items = cast("dict[str, object]", node)
        found = [value for key, value in items.items() if key == "pattern" and isinstance(value, str)]
        return found + [p for value in items.values() for p in _patterns(value)]
    if isinstance(node, list):
        return [p for item in cast("list[object]", node) for p in _patterns(item)]
    return []


def _differs(pattern: str) -> str | None:
    """What in pattern pydantic-core's regex (which checks requests) reads differently from ECMA-262 (which the
    contract's clients read): \\d, \\w, \\s and \\b are Unicode in one and ASCII or another set in the other, and `.`
    excludes different line terminators. None when the pattern means the same to both."""
    in_class = False
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "\\":
            if pattern[i + 1 : i + 2] in set("dDwWsSbB"):
                return pattern[i : i + 2]
            i += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == ".":
            return "."
        i += 1
    return None


def test_patterns_mean_the_same_to_the_server_and_its_clients() -> None:
    # write explicit character classes ([A-Za-z0-9]) instead (docs/rules/api-design.md)
    patterns = _patterns(spec())
    assert patterns  # CreateUserRequest.account has one
    assert {p: _differs(p) for p in patterns if _differs(p)} == {}
    assert [_differs(p) for p in (r"^\w+$", r"^a.b$", r"^[.]\d$", r"^[A-Za-z0-9_.-]+$")] == [r"\w", ".", r"\d", None]
