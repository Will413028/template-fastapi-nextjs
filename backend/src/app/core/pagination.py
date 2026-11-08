"""Keyset pagination: list operations return rows newest first and an opaque cursor for the next page.

A cursor is the 16 bytes of the page's last id in unpadded base64url. Clients pass it back unchanged and never build or
parse one (docs/rules/api-design.md).
"""

import base64
import re
import uuid
from typing import TYPE_CHECKING

from app.core.errors import AppError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

# 16 bytes are 22 base64url characters without padding.
_CURSOR = re.compile(r"[A-Za-z0-9_-]{22}")


# The contract's Limit default (components.parameters.Limit), for every list operation; test_pagination checks it.
DEFAULT_LIMIT = 20
# The largest page a list operation returns (app.http.schemas.Limit).
MAX_LIMIT = 100


def encode_cursor(row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(row_id.bytes).rstrip(b"=").decode()


def decode_cursor(cursor: str) -> uuid.UUID:
    """The id a cursor from encode_cursor holds. Anything else is a validation error on the cursor parameter."""
    if not _CURSOR.fullmatch(cursor):
        raise _invalid_cursor()
    # the pattern admits only 22 characters that decode to exactly 16 bytes
    return uuid.UUID(bytes=base64.urlsafe_b64decode(cursor + "=="))


def after(cursor: str | None) -> uuid.UUID | None:
    """The optional cursor parameter: None for the first page."""
    return None if cursor is None else decode_cursor(cursor)


def page_rows(limit: int) -> int:
    """The row limit for a page of limit items: one more, so trim_page knows whether another page follows without a
    second query or a count."""
    return limit + 1


def trim_page[T](rows: Sequence[T], limit: int, row_id: Callable[[T], uuid.UUID]) -> tuple[list[T], str | None]:
    """Cuts rows read with page_rows(limit) to the page, and returns the next page's cursor: None when no extra row
    came back, so the last page has no cursor."""
    if len(rows) <= limit:
        return list(rows), None
    return list(rows[:limit]), encode_cursor(row_id(rows[limit - 1]))


def _invalid_cursor() -> AppError:
    return AppError.invalid("/cursor", "invalid", "cursor is not one this API returned")
