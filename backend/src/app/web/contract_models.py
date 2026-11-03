# Generated from api/openapi.yaml by `make gen` (options: pyproject.toml [tool.datamodel-codegen]); do not edit.
# `make gen-check` fails when this file no longer matches the contract.

from typing import Annotated, Literal
from uuid import UUID

from pydantic import MISSING, AwareDatetime, BaseModel, ConfigDict, Field


class User(BaseModel):
    id: UUID
    account: str
    """
    Lowercase; accounts are unique regardless of case.
    """
    role: Literal["admin", "user"]
    is_disabled: bool
    created_at: AwareDatetime


class UserPage(BaseModel):
    users: list[User]
    next_cursor: Annotated[str | None, Field(...)]
    """
    Cursor for the next page; null on the last page.
    """


class Session(BaseModel):
    """
    A signed-in session. The token is only in the cookie and never part of the resource.
    """

    id: UUID
    user: User
    created_at: AwareDatetime
    last_used_at: AwareDatetime
    """
    Updated at most once a minute. A session unused for the idle limit ends before `expires_at`.

    """
    expires_at: AwareDatetime
    """
    The absolute end of the session, however much it is used.
    """
    current: bool
    """
    Whether this is the session the request carries.
    """


class SessionPage(BaseModel):
    sessions: list[Session]
    next_cursor: Annotated[str | None, Field(...)]
    """
    Cursor for the next page; null on the last page.
    """


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    account: Annotated[str, Field(max_length=50, min_length=3, pattern="^[A-Za-z0-9_.-]+$")]
    """
    Stored lowercase.
    """
    password: Annotated[str, Field(max_length=128, min_length=15)]
    """
    15 to 128 characters (code points), no composition rules (NIST SP 800-63B-4). A password on the common or
    breached password list is rejected with code `password_too_common`.

    """


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    account: Annotated[str, Field(max_length=50, min_length=1)]
    """
    Case-insensitive.
    """
    password: Annotated[str, Field(max_length=128, min_length=1)]


class SignInLock(BaseModel):
    locked: bool
    """
    Sign-ins from clients without a device cookie for the account are refused.
    """
    failures: Annotated[int, Field(ge=0)]
    """
    Failed sign-ins from such clients since the last successful one; the lock starts at 100.
    """


class UserPatch(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    is_disabled: bool | MISSING = MISSING


class Item(BaseModel):
    id: UUID
    title: str
    description: Annotated[str | None, Field(...)]
    owner_id: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ItemPage(BaseModel):
    items: list[Item]
    next_cursor: Annotated[str | None, Field(...)]
    """
    Cursor for the next page; null on the last page.
    """


class Problem(BaseModel):
    """
    RFC 9457 problem details.
    """

    type: Annotated[str, Field(examples=["/problems/not-found"])]
    """
    Fixed per kind of problem. The list grows (x-extensible-enum), so clients translate the values they know and fall back for the rest.
    """
    title: str
    """
    Fixed per `type`.
    """
    status: int
    """
    The HTTP status code.
    """
    detail: str | MISSING = MISSING
    """
    Human-readable explanation for developers; clients must not parse it.
    """
    code: str | MISSING = MISSING
    """
    Stable machine-readable code refining `type`. The list grows (x-extensible-enum), so clients translate the values they know and fall back for the rest.
    """
    request_id: str | MISSING = MISSING
    """
    The request's `X-Request-ID`, for matching a report with the server logs.
    """


class FieldError(BaseModel):
    pointer: Annotated[str, Field(examples=["/title"])]
    """
    JSON Pointer (RFC 6901) to the invalid value in the request body, or `/<name>` for a query or path
    parameter. Empty when the whole body is missing.

    """
    code: Annotated[str, Field(examples=["too_short"])]
    """
    Stable machine-readable code: a failed contract rule, or a business rule such as `password_too_common`.
    The list grows (x-extensible-enum), so clients translate the values they know and fall back for the rest.

    """
    detail: str
    """
    Human-readable explanation for developers; clients must not parse it.
    """


class CreateItemRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    title: Annotated[str, Field(max_length=200, min_length=1)]
    """
    1 to 200 characters (code points).
    """
    description: str | None | MISSING = MISSING


class ItemPatch(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    title: Annotated[str | MISSING, Field(max_length=200, min_length=1)] = MISSING
    """
    1 to 200 characters (code points).
    """
    description: str | None | MISSING = MISSING


class ValidationProblem(Problem):
    """
    A problem with type `/problems/validation`, listing each invalid input.
    """

    errors: list[FieldError]
