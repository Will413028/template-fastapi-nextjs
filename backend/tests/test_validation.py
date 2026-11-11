"""Request checks before an operation runs (Go version: internal/httpapi/validate_test.go): the body limit
(app.http.body_limit), the route checks (app.http.route) and the 422 codes (app.http.errors), against the stub widget
operations of tests/apitest.py."""

import asyncio
import dataclasses
import json
import logging
from typing import TYPE_CHECKING, Annotated, Any, get_args

import pytest
from fastapi import Body, Response
from pydantic_core.core_schema import ErrorType

from apitest import (
    GENERATED_CONTRACT_PATH,
    LOOPBACK,
    assert_problem,
    bare_app,
    call_asgi,
    checked_client,
    deps,
    stub_app,
)
from app.http.body_limit import MAX_BODY_BYTES
from app.http.errors import VALIDATION_CODES, field_error
from app.http.problems import FIELD_ERROR_CODES
from app.main import create_app
from app.modules.auth.schemas import CreateUserRequest
from authfake import FakeAuthService, fake_app, fake_modules

if TYPE_CHECKING:
    from starlette.types import Message

WIDGET_ID = "019a0f3e-7c4b-7d2a-9b1e-3f5a6c7d8e9f"
MERGE_PATCH = {"content-type": "application/merge-patch+json"}


def test_valid_request_reaches_the_operation() -> None:
    body = '{"name":"abc","size":2}'
    with checked_client(stub_app()) as client:
        response = client.post("/api/widgets", content=body, headers={"content-type": "application/json"})
        assert response.status_code == 201
        assert response.text == body
        for target in ("/api/widgets?limit=5", f"/api/widgets/{WIDGET_ID}"):
            assert client.get(target).status_code == 200, target


def test_merge_patch_body_is_parsed_as_json() -> None:
    with checked_client(stub_app()) as client:
        ok = client.patch(f"/api/widgets/{WIDGET_ID}", content='{"name":"abc"}', headers=MERGE_PATCH)
        assert ok.status_code == 200, ok.text
        bad = client.patch(f"/api/widgets/{WIDGET_ID}", content='{"name":"ab"}', headers=MERGE_PATCH)
    problem = assert_problem(bad, 422, "/problems/validation")
    assert [(e["pointer"], e["code"]) for e in problem["errors"]] == [("/name", "too_short")]


def errors_of(method: str, target: str, body: str | None) -> list[dict[str, Any]]:
    headers = {"content-type": "application/json"} if method == "POST" else {}
    with checked_client(stub_app()) as client:
        response = client.request(method, target, content=body, headers=headers)
    problem = assert_problem(response, 422, "/problems/validation")
    return problem["errors"]


@pytest.mark.parametrize(
    ("method", "target", "body", "pointer", "code"),
    [
        ("POST", "/api/widgets", "{}", "/name", "required"),
        ("POST", "/api/widgets", '{"name":"ab"}', "/name", "too_short"),
        ("POST", "/api/widgets", '{"name":"abcdefghijk"}', "/name", "too_long"),
        ("POST", "/api/widgets", '{"name":"abc","tags":["a","b","c"]}', "/tags", "too_long"),
        ("POST", "/api/widgets", '{"name":"abc","slug":"ABC"}', "/slug", "pattern"),
        ("POST", "/api/widgets", '{"name":"abc","color":"green"}', "/color", "enum"),
        ("POST", "/api/widgets", '{"name":"abc","ref":"not-a-uuid"}', "/ref", "format"),
        ("POST", "/api/widgets", '{"name":"abc","size":9}', "/size", "out_of_range"),
        ("POST", "/api/widgets", '{"name":"abc","size":"big"}', "/size", "invalid_type"),
        # JSON Schema types are strict: a string is not an integer, whatever it spells
        ("POST", "/api/widgets", '{"name":"abc","size":"3"}', "/size", "invalid_type"),
        ("POST", "/api/widgets", '{"name":5}', "/name", "invalid_type"),
        ("POST", "/api/widgets", '{"name":"abc","note":null}', "/note", "null_not_allowed"),
        ("POST", "/api/widgets", '{"name":"abc","extra":1}', "/extra", "additional_property"),
        # an undeclared property is not allowed whatever its value, null included
        ("POST", "/api/widgets", '{"name":"abc","extra":null}', "/extra", "additional_property"),
        ("POST", "/api/widgets", '{"name":"abc","parts":{"a/b":"xy"}}', "/parts/a~1b", "too_long"),
        ("POST", "/api/widgets", '{"name":"abc","count":3}', "/count", "invalid"),
        ("POST", "/api/widgets", "", "", "required"),
        ("GET", "/api/widgets?limit=0", None, "/limit", "out_of_range"),
        ("GET", "/api/widgets?limit=abc", None, "/limit", "invalid_type"),
        ("GET", "/api/widgets?limit=", None, "/limit", "invalid"),
        ("GET", "/api/widgets?cursor=", None, "/cursor", "invalid"),
        ("GET", "/api/widgets/not-a-uuid", None, "/widget_id", "format"),
        # the hyphenated form only, as the contract's format: uuid reads it
        ("GET", "/api/widgets/019a0f3e7c4b7d2a9b1e3f5a6c7d8e9f", None, "/widget_id", "format"),
        ("GET", "/api/widgets/{019a0f3e-7c4b-7d2a-9b1e-3f5a6c7d8e9f}", None, "/widget_id", "format"),
        ("GET", "/api/widgets/urn:uuid:019a0f3e-7c4b-7d2a-9b1e-3f5a6c7d8e9f", None, "/widget_id", "format"),
    ],
)
def test_validation_codes(method: str, target: str, body: str | None, pointer: str, code: str) -> None:
    errors = errors_of(method, target, body)
    assert [(e["pointer"], e["code"]) for e in errors] == [(pointer, code)]
    assert errors[0]["detail"]


def test_validation_detail_does_not_echo_the_input() -> None:
    # a detail may end up in a client's error report; the value can be a password
    for body in ('{"name":"abc","slug":"Canary7f3a"}', '{"name":"abc","color":"Canary7f3a"}'):
        [error] = errors_of("POST", "/api/widgets", body)
        assert "Canary7f3a" not in error["detail"], body


def test_validation_lists_every_error() -> None:
    errors = errors_of("POST", "/api/widgets", '{"name":"ab","size":9,"x":1,"y":2}')
    assert sorted((e["pointer"], e["code"]) for e in errors) == [
        ("/name", "too_short"),
        ("/size", "out_of_range"),
        ("/x", "additional_property"),
        ("/y", "additional_property"),
    ]


def test_parameter_and_body_errors_are_listed_together() -> None:
    with checked_client(stub_app()) as client:
        response = client.patch("/api/widgets/not-a-uuid", content='{"name":"ab"}', headers=MERGE_PATCH)
    errors = assert_problem(response, 422, "/problems/validation")["errors"]
    assert [(e["pointer"], e["code"]) for e in errors] == [("/widget_id", "format"), ("/name", "too_short")]


def test_a_missing_parameter_is_required() -> None:
    app = bare_app()

    @app.get("/api/widgets")
    async def list_widgets(limit: int) -> Response:
        return Response(str(limit))

    with checked_client(app) as client:
        response = client.get("/api/widgets")
    errors = assert_problem(response, 422, "/problems/validation")["errors"]
    assert errors == [{"pointer": "/limit", "code": "required", "detail": "value is required"}]


def test_every_validation_code_is_a_pydantic_error_type_and_a_contract_code() -> None:
    # a misspelt type would never match; a code outside the contract would fail a client's type check
    assert set(VALIDATION_CODES) <= set(get_args(ErrorType))
    assert set(VALIDATION_CODES.values()) | {"null_not_allowed", "invalid", "password_too_common"} == set(
        FIELD_ERROR_CODES
    )


def test_an_error_type_without_a_code_is_invalid_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    # so a new kind of error is noticed instead of failing the request (the old validator answered 500)
    with caplog.at_level(logging.WARNING, logger="app.http.errors"):
        field = field_error({"type": "frozen_field", "loc": ("body", "name"), "input": "x", "msg": "x"})
    assert (field.pointer, field.code, field.detail) == ("/name", "invalid", "invalid value")
    assert [getattr(r, "error_type", None) for r in caplog.records] == ["frozen_field"]


@pytest.mark.parametrize("content_type", ["text/x-canary", None])
def test_unsupported_media_type(content_type: str | None) -> None:
    headers = {"content-type": content_type} if content_type else {}
    with checked_client(stub_app()) as client:
        response = client.post("/api/widgets", content='{"name":"abc"}', headers=headers)
    problem = assert_problem(response, 415, "/problems/unsupported-media-type")
    assert "canary" not in response.text
    assert problem["detail"] == "accepted Content-Type: application/json"
    assert "accept-patch" not in response.headers


def test_a_patch_in_another_media_type_names_merge_patch() -> None:
    # RFC 5789: a 415 to a PATCH says in Accept-Patch which patch documents the resource takes
    with checked_client(stub_app()) as client:
        response = client.patch(f"/api/widgets/{WIDGET_ID}", json={"name": "abc"})
    assert_problem(response, 415, "/problems/unsupported-media-type")
    assert response.headers["accept-patch"] == "application/merge-patch+json"


def test_media_type_parameters_are_accepted() -> None:
    with checked_client(stub_app()) as client:
        response = client.post(
            "/api/widgets", content='{"name":"abc"}', headers={"content-type": "Application/JSON; charset=utf-8"}
        )
    assert response.status_code == 201


@pytest.mark.parametrize(
    "body",
    [
        '{"name":',
        '{"name":"abc"} trailing',
        '{"name":"abc"}{}',
        # Python's json reads these JavaScript constants; JSON (RFC 8259) has no such values
        '{"name":"abc","size":NaN}',
        '{"name":"abc","size":Infinity}',
        "NaN",
        b'{"name":"ab\xe9"}',  # not UTF-8
    ],
)
def test_malformed_json_is_a_bad_request(body: str | bytes) -> None:
    with checked_client(stub_app()) as client:
        response = client.post("/api/widgets", content=body, headers={"content-type": "application/json"})
    problem = assert_problem(response, 400, "/problems/bad-request")
    assert problem["detail"] == "request body is not valid JSON"


def test_a_missing_body_is_required_whatever_its_content_type() -> None:
    # no body has no media type to refuse: the old validator also answered 422 `required`
    with checked_client(stub_app()) as client:
        for headers in ({}, {"content-type": "text/plain"}):
            response = client.post("/api/widgets", content=b"", headers=headers)
            errors = assert_problem(response, 422, "/problems/validation")["errors"]
            assert [(e["pointer"], e["code"]) for e in errors] == [("", "required")], headers


def test_a_body_in_another_media_type_is_not_parsed_as_json() -> None:
    app = bare_app()

    @app.post("/api/notes", status_code=201)
    async def create_note(body: Annotated[str, Body(media_type="text/plain")]) -> dict[str, str]:
        return {"note": body}

    with checked_client(app) as client:
        response = client.post("/api/notes", content="not {json", headers={"content-type": "text/plain"})
    assert response.status_code == 201, response.text
    assert response.json() == {"note": "not {json"}


def test_a_media_type_error_comes_before_a_json_error() -> None:
    with checked_client(stub_app()) as client:
        response = client.post("/api/widgets", content='{"name":', headers={"content-type": "text/plain"})
    assert_problem(response, 415, "/problems/unsupported-media-type")


def test_body_read_failure_is_not_a_validation_error() -> None:
    # the client went away mid-body: not the client's input, so not a 4xx
    scope = {"method": "POST", "path": "/api/widgets", "headers": [(b"content-type", b"application/json")]}
    sent = asyncio.run(call_asgi(stub_app(), scope, [{"type": "http.request", "body": b'{"na', "more_body": True}]))
    assert sent[0]["status"] == 500
    assert json.loads(sent[1]["body"])["type"] == "/problems/internal"


def test_undefined_route() -> None:
    with checked_client(stub_app()) as client:
        assert_problem(client.get("/api/nope"), 404, "/problems/not-found")
        not_allowed = client.delete("/api/widgets")
        assert_problem(not_allowed, 405, "/problems/method-not-allowed")
        # Starlette's routing sends Allow (the old validator sent none), listing the methods of the first route that
        # matched the path only: GET /api/widgets is another route
        assert not_allowed.headers["allow"] == "POST"
        # only /api/widgets exists, not /widgets
        assert_problem(client.get("/widgets"), 404, "/problems/not-found")
        # a trailing slash is another path, not a redirect to this one
        assert_problem(client.get("/api/widgets/", follow_redirects=False), 404, "/problems/not-found")


def test_body_over_the_limit() -> None:
    big = '{"name":"' + "a" * MAX_BODY_BYTES + '"}'
    with checked_client(stub_app()) as client:
        response = client.post("/api/widgets", content=big, headers={"content-type": "application/json"})
        # before routing: an undefined route and a GET are refused too
        undefined = client.request("GET", "/api/nope", content=big)
    assert (
        assert_problem(response, 413, "/problems/payload-too-large")["detail"] == "request body is over 1048576 bytes"
    )
    assert_problem(undefined, 413, "/problems/payload-too-large")


def test_body_over_the_limit_without_content_length() -> None:
    # a chunked body announces no length: the limit applies while reading
    chunk = b"a" * (MAX_BODY_BYTES // 2)
    scope = {"method": "POST", "path": "/api/widgets", "headers": [(b"content-type", b"application/json")]}
    messages: list[Message] = [{"type": "http.request", "body": chunk, "more_body": True} for _ in range(3)]
    sent = asyncio.run(call_asgi(stub_app(), scope, messages))
    assert sent[0]["status"] == 413


def test_failure_in_an_operation_is_a_problem() -> None:
    with checked_client(stub_app()) as client:
        assert_problem(client.get("/api/fail"), 500, "/problems/internal")


def test_secured_operation_without_a_session_is_unauthenticated() -> None:
    # access is checked before parameters and body, so an anonymous caller learns nothing about the request it would
    # have to send; one 401, with code session_invalid, for every missing or dead session
    with checked_client(fake_app(FakeAuthService())) as client:
        response = client.get("/api/users/me")
    problem = assert_problem(response, 401, "/problems/unauthenticated")
    assert problem["code"] == "session_invalid"


FULL_WIDTH_ABC = "\uff41\uff42\uff43"


@pytest.mark.parametrize("account", ["abc\n", "abc\ndef", FULL_WIDTH_ABC])
def test_pattern_dollar_matches_only_the_end(account: str) -> None:
    # OpenAPI patterns are ECMA-262: `$` ends the string. Python's re also lets it match before a final newline, so
    # "abc\n" would pass ^[A-Za-z0-9_.-]+$ and reach the database (design review 2026-11-03); pydantic-core's regex,
    # like the Go version's RE2, agrees with ECMA. Full-width letters are not A-Z either.
    body = {"account": account, "password": "a long enough password"}
    with checked_client(fake_app(FakeAuthService())) as client:
        response = client.post("/api/users", json=body, headers={"Sec-Fetch-Site": "same-origin"})
    errors = assert_problem(response, 422, "/problems/validation")["errors"]
    assert [(e["pointer"], e["code"]) for e in errors] == [("/account", "pattern")]


def test_a_matching_pattern_passes_validation() -> None:
    body = CreateUserRequest.model_validate({"account": "a.b_c-1", "password": "a long enough password"})
    assert body.account == "a.b_c-1"


def test_health_is_outside_validation() -> None:
    with checked_client(stub_app(), peer=LOOPBACK) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}


def test_docs_only_in_dev() -> None:
    with checked_client(stub_app(docs=True)) as client:
        spec = client.get("/api/openapi.json")
        assert spec.status_code == 200
        assert spec.headers["content-type"] == "application/json"
        docs = client.get("/api/docs")
        assert docs.status_code == 200
        assert 'integrity="sha384-' in docs.text
        assert '"/api/openapi.json"' in docs.text
    # MODE=prod serves neither: a deployed API does not publish its contract. The real routes, so FastAPI's openapi_url
    # being on would fail the route check (a route outside the skipped docs paths that is not an ApiRoute).
    app = create_app(dataclasses.replace(deps(), modules=fake_modules()))
    with checked_client(app) as client:
        for path in ("/api/openapi.json", "/api/docs"):
            assert_problem(client.get(path), 404, "/problems/not-found")


def test_docs_serve_the_generated_contract() -> None:
    app = create_app(dataclasses.replace(deps(), docs=True, modules=fake_modules()))
    with checked_client(app) as client:
        assert client.get("/api/openapi.json").json() == json.loads(GENERATED_CONTRACT_PATH.read_text(encoding="utf-8"))
