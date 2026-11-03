"""How services report expected failures: a kind that decides the HTTP status and problem type, and a stable code
clients translate.

Services raise AppError; app.web.problems turns it into an RFC 9457 problem in one place. Any other exception is
unexpected and becomes a 500 whose details stay in the log.
"""

import enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import timedelta


class Kind(enum.Enum):
    """The category of failure; each maps to one problem type."""

    NOT_FOUND = enum.auto()
    CONFLICT = enum.auto()
    UNAUTHENTICATED = enum.auto()
    FORBIDDEN = enum.auto()
    VALIDATION = enum.auto()
    RATE_LIMITED = enum.auto()


class AppError(Exception):
    """An expected failure.

    code is the stable machine-readable code clients translate, for example "account_taken"; changing it is a
    breaking change. detail explains the failure to developers and is sent to the client, so it must not contain
    secrets or internal state, and clients must not parse it. pointer is the JSON Pointer to the invalid input, for
    VALIDATION; retry_after is how long the client should wait, for RATE_LIMITED.
    """

    def __init__(
        self,
        kind: Kind,
        code: str,
        detail: str = "",
        *,
        pointer: str = "",
        retry_after: timedelta | None = None,
    ) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.kind = kind
        self.code = code
        self.detail = detail
        self.pointer = pointer
        self.retry_after = retry_after

    @classmethod
    def invalid(cls, pointer: str, code: str, detail: str) -> AppError:
        """A VALIDATION error on the input at pointer, for rules the contract cannot express (for example a password
        on the common-password list)."""
        return cls(Kind.VALIDATION, code, detail, pointer=pointer)
