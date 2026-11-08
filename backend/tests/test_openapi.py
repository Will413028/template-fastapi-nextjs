"""The contract generated from the routes (app.main.build_openapi): problems are application/problem+json, FastAPI's
own validation schema stays out, and each operation's security follows its access marker. make gen-check keeps the
committed api/openapi.json equal to it."""

import functools
from http import HTTPStatus
from typing import Any

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
