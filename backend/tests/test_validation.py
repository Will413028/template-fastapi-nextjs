"""Request validation against the contract (Go version: internal/httpapi/validate_test.go)."""

import asyncio
import dataclasses
import json
from typing import TYPE_CHECKING, Annotated, Any

import pytest
from fastapi import Path, Response
from fastapi.testclient import TestClient

from apitest import (
    CONTRACT_PATH,
    LOOPBACK,
    PEER,
    TEST_CONTRACT_PATH,
    assert_problem,
    bare_app,
    call_asgi,
    checked_client,
    contract,
    deps,
    stub_app,
)
from app.http.contract import Contract, ContractError, ecma_regex
from app.http.validation import MAX_BODY_BYTES
from app.main import create_app

if TYPE_CHECKING:
    import pathlib

    from starlette.types import Message

WIDGET_ID = "019a0f3e-7c4b-7d2a-9b1e-3f5a6c7d8e9f"


def test_valid_request_reaches_the_operation() -> None:
    body = '{"name":"abc","size":2}'
    with checked_client(stub_app()) as client:
        response = client.post("/api/widgets", content=body, headers={"content-type": "application/json"})
        assert response.status_code == 201
        assert response.text == body
        for target in ("/api/widgets?limit=5", f"/api/widgets/{WIDGET_ID}"):
            assert client.get(target).status_code == 200, target


def test_merge_patch_body_is_parsed_as_json() -> None:
    # openapi-core reads application/merge-patch+json as bytes unless a JSON deserializer is registered for it
    with checked_client(stub_app()) as client:
        ok = client.patch(
            f"/api/widgets/{WIDGET_ID}",
            content='{"name":"abc"}',
            headers={"content-type": "application/merge-patch+json"},
        )
        assert ok.status_code == 200, ok.text
        bad = client.patch(
            f"/api/widgets/{WIDGET_ID}",
            content='{"name":"ab"}',
            headers={"content-type": "application/merge-patch+json"},
        )
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
        ("POST", "/api/widgets", '{"name":"abc","note":null}', "/note", "null_not_allowed"),
        ("POST", "/api/widgets", '{"name":"abc","extra":1}', "/extra", "additional_property"),
        ("POST", "/api/widgets", '{"name":"abc","parts":{"a/b":"xy"}}', "/parts/a~1b", "too_long"),
        ("POST", "/api/widgets", '{"name":"abc","count":3}', "/count", "invalid"),
        ("POST", "/api/widgets", "", "", "required"),
        ("GET", "/api/widgets?limit=0", None, "/limit", "out_of_range"),
        ("GET", "/api/widgets?limit=abc", None, "/limit", "invalid_type"),
        ("GET", "/api/widgets?limit=", None, "/limit", "invalid"),
        ("GET", "/api/widgets?cursor=", None, "/cursor", "invalid"),
        ("GET", "/api/widgets/not-a-uuid", None, "/id", "format"),
    ],
)
def test_validation_codes(method: str, target: str, body: str | None, pointer: str, code: str) -> None:
    errors = errors_of(method, target, body)
    assert [(e["pointer"], e["code"]) for e in errors] == [(pointer, code)]
    assert errors[0]["detail"]


def test_validation_detail_does_not_echo_the_input() -> None:
    # a detail may end up in a client's error report; the value can be a password
    [error] = errors_of("POST", "/api/widgets", '{"name":"abc","slug":"Canary7f3a"}')
    assert "Canary7f3a" not in error["detail"]


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
        response = client.patch(
            "/api/widgets/not-a-uuid", content='{"name":"ab"}', headers={"content-type": "application/merge-patch+json"}
        )
    errors = assert_problem(response, 422, "/problems/validation")["errors"]
    assert [(e["pointer"], e["code"]) for e in errors] == [("/id", "format"), ("/name", "too_short")]


@pytest.mark.parametrize("content_type", ["text/x-canary", None])
def test_unsupported_media_type(content_type: str | None) -> None:
    headers = {"content-type": content_type} if content_type else {}
    with checked_client(stub_app()) as client:
        response = client.post("/api/widgets", content='{"name":"abc"}', headers=headers)
    problem = assert_problem(response, 415, "/problems/unsupported-media-type")
    assert "canary" not in response.text
    assert "application/json" in problem["detail"]


def test_media_type_parameters_are_accepted() -> None:
    with checked_client(stub_app()) as client:
        response = client.post(
            "/api/widgets", content='{"name":"abc"}', headers={"content-type": "application/json; charset=utf-8"}
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
        b'{"name":"ab\xe9"}',  # not UTF-8
    ],
)
def test_malformed_json_is_a_bad_request(body: str | bytes) -> None:
    with checked_client(stub_app()) as client:
        response = client.post("/api/widgets", content=body, headers={"content-type": "application/json"})
    problem = assert_problem(response, 400, "/problems/bad-request")
    assert problem["detail"] == "request body is not valid JSON"


def test_body_read_failure_is_not_a_validation_error() -> None:
    # the client went away mid-body: not the client's input, so not a 4xx
    scope = {"method": "POST", "path": "/api/widgets", "headers": [(b"content-type", b"application/json")]}
    sent = asyncio.run(call_asgi(stub_app(), scope, [{"type": "http.request", "body": b'{"na', "more_body": True}]))
    assert sent[0]["status"] == 500
    assert json.loads(sent[1]["body"])["type"] == "/problems/internal"


def test_undefined_route() -> None:
    with checked_client(stub_app()) as client:
        assert_problem(client.get("/api/nope"), 404, "/problems/not-found")
        assert_problem(client.delete("/api/widgets"), 405, "/problems/method-not-allowed")
        # the contract's paths have no /api prefix; only /api/widgets exists, not /widgets
        assert_problem(client.get("/widgets"), 404, "/problems/not-found")


def test_body_over_the_limit() -> None:
    big = '{"name":"' + "a" * MAX_BODY_BYTES + '"}'
    with checked_client(stub_app()) as client:
        response = client.post("/api/widgets", content=big, headers={"content-type": "application/json"})
    assert_problem(response, 413, "/problems/payload-too-large")


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


def test_binding_failure_after_validation_is_a_validation_problem() -> None:
    # The route's own parsing can be stricter than the contract (here an int where the contract has a uuid string);
    # its failure is still invalid input in the problem shape, never FastAPI's {"detail": [...]}.
    app = bare_app()

    @app.get("/api/widgets/{id}")
    async def get_widget(widget_id: Annotated[int, Path(alias="id")]) -> Response:
        return Response(str(widget_id))

    with checked_client(app) as client:
        response = client.get(f"/api/widgets/{WIDGET_ID}")
    errors = assert_problem(response, 422, "/problems/validation")["errors"]
    assert errors == [
        {"pointer": "/id", "code": "invalid_type", "detail": "value cannot be parsed as the declared type"}
    ]


def test_secured_operation_without_a_session_is_unauthenticated() -> None:
    # The contract's default security is the session cookie; security is checked before parameters and body, so an
    # anonymous caller learns nothing about the request it would have to send.
    app = create_app(deps(contract()))
    with checked_client(app, against=contract()) as client:
        response = client.get("/api/users/me")
    problem = assert_problem(response, 401, "/problems/unauthenticated")
    assert problem["detail"] == "this operation needs a signed-in session"


FULL_WIDTH_ABC = "\uff41\uff42\uff43"


@pytest.mark.parametrize("account", ["abc\n", "abc\ndef", FULL_WIDTH_ABC])
def test_pattern_dollar_matches_only_the_end(account: str) -> None:
    # OpenAPI patterns are ECMA-262: `$` ends the string. Python's re also lets it match before a final newline, so
    # "abc\n" would pass ^[A-Za-z0-9_.-]+$ and reach the database (design review 2026-11-03); the Go version's RE2
    # agrees with ECMA. Full-width letters are not A-Z either.
    app = create_app(deps(contract()))
    body = {"account": account, "password": "a long enough password"}
    with checked_client(app, against=contract()) as client:
        response = client.post("/api/users", json=body)
    errors = assert_problem(response, 422, "/problems/validation")["errors"]
    assert [(e["pointer"], e["code"]) for e in errors] == [("/account", "pattern")]


def test_a_matching_pattern_passes_validation() -> None:
    app = create_app(deps(contract()))
    body = {"account": "abc", "password": "a long enough password"}
    # past validation: createUser has no route in this app, and a 404 is not one of its contract responses, so the
    # client does not check responses against the contract
    with TestClient(app, client=PEER) as client:
        assert_problem(client.post("/api/users", json=body), 404, "/problems/not-found")


def test_health_is_outside_validation() -> None:
    with checked_client(stub_app(), peer=LOOPBACK) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}


def test_docs_only_in_dev() -> None:
    with checked_client(stub_app(docs=True)) as client:
        spec = client.get("/api/openapi.yaml")
        assert spec.status_code == 200
        assert spec.headers["content-type"] == "application/yaml"
        docs = client.get("/api/docs")
        assert docs.status_code == 200
        assert 'integrity="sha384-' in docs.text
    with checked_client(stub_app()) as client:
        for path in ("/api/openapi.yaml", "/api/docs"):
            assert_problem(client.get(path), 404, "/problems/not-found")


def test_docs_serve_the_contract_file() -> None:
    app = create_app(dataclasses.replace(deps(contract()), docs=True))
    with checked_client(app, against=contract()) as client:
        assert client.get("/api/openapi.yaml").content == CONTRACT_PATH.read_bytes()


def test_binding_failure_on_a_missing_value_is_required() -> None:
    # the contract leaves limit optional, the route requires it
    app = bare_app()

    @app.get("/api/widgets")
    async def list_widgets(limit: int) -> Response:
        return Response(str(limit))

    with checked_client(app) as client:
        response = client.get("/api/widgets")
    errors = assert_problem(response, 422, "/problems/validation")["errors"]
    assert errors == [{"pointer": "/limit", "code": "required", "detail": "value is required"}]


@pytest.mark.parametrize(
    ("pattern", "value", "matches"),
    [
        (r"^\S+$", "a\u3000b", False),  # ECMA's \s includes Unicode spaces such as U+3000
        (r"^\S+$", "ab", True),
        (r"^[\s]$", "\u00a0", True),
        (r"^.$", "\r", False),  # ECMA's . excludes line terminators
        (r"^[.]$", ".", True),
        (r"^\d+$", "\u0663", False),  # \d is ASCII in ECMA (U+0663 is an Arabic-Indic digit)
    ],
)
def test_patterns_have_their_ecma_meaning(pattern: str, value: str, *, matches: bool) -> None:
    assert bool(ecma_regex(pattern).search(value)) is matches


@pytest.mark.parametrize(
    ("pattern", "message"),
    [
        # Python's re cannot compile ECMA's named groups: the OpenAPI validator refuses the contract
        (r"(?<name>a)", "properties"),
        # re can, but the translation cannot give it ECMA's meaning
        (r"[\S]", r"\\S inside a character class is not supported"),
    ],
)
def test_a_pattern_without_its_ecma_meaning_fails_at_load(tmp_path: pathlib.Path, pattern: str, message: str) -> None:
    # at startup, not on the first request that reaches the pattern
    text = TEST_CONTRACT_PATH.read_text(encoding="utf-8").replace(
        'pattern: "^[a-z]+$"', f"pattern: {json.dumps(pattern)}"
    )
    changed = tmp_path / "openapi.yaml"
    changed.write_text(text, encoding="utf-8")
    with pytest.raises(ContractError, match=message):
        Contract.load(changed)
