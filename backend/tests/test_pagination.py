"""Keyset pagination cursors (Go version: internal/platform/httpx/cursor_test.go)."""

import base64
import uuid

import pytest
import yaml

from apitest import CONTRACT_PATH
from app.core.errors import AppError, Kind
from app.core.pagination import DEFAULT_LIMIT, after, decode_cursor, encode_cursor, page_rows, trim_page


def test_cursor_round_trip() -> None:
    row_id = uuid.uuid7()
    assert decode_cursor(encode_cursor(row_id)) == row_id


_ID = uuid.UUID("019a0f3e-7c4b-7d2a-9b1e-3f5a6c7d8e9f")
_VALID = encode_cursor(_ID)


@pytest.mark.parametrize(
    "cursor",
    [
        pytest.param(_VALID[:10] + "*" + _VALID[11:], id="not base64url"),
        pytest.param(base64.urlsafe_b64encode(_ID.bytes).decode(), id="padded base64"),
        pytest.param(base64.b64encode(b"\xfb" * 16).decode().rstrip("="), id="standard alphabet"),
        pytest.param(base64.urlsafe_b64encode(_ID.bytes[:15]).decode().rstrip("="), id="one byte short"),
        pytest.param(base64.urlsafe_b64encode(_ID.bytes + b"\0").decode().rstrip("="), id="one byte long"),
        pytest.param(_VALID[:-1] + " " + _VALID[-1], id="whitespace inside"),
        pytest.param("", id="empty"),
    ],
)
def test_tampered_cursor_is_invalid(cursor: str) -> None:
    with pytest.raises(AppError) as caught:
        decode_cursor(cursor)
    assert caught.value.kind is Kind.VALIDATION
    assert caught.value.pointer == "/cursor"
    assert caught.value.code == "invalid"


def test_after_is_none_for_the_first_page() -> None:
    assert after(None) is None
    assert after(_VALID) == _ID


def test_trim_page() -> None:
    ids = [uuid.uuid7() for _ in range(4)]
    assert page_rows(3) == 4
    # one extra row came back: another page follows, starting after the page's last row
    rows, next_cursor = trim_page(ids, 3, lambda row: row)
    assert rows == ids[:3]
    assert next_cursor is not None
    assert decode_cursor(next_cursor) == ids[2]
    # no extra row: the last page has no cursor
    assert trim_page(ids[:3], 3, lambda row: row) == (ids[:3], None)


def test_the_default_limit_is_the_contracts() -> None:
    document = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert document["components"]["parameters"]["Limit"]["schema"]["default"] == DEFAULT_LIMIT
