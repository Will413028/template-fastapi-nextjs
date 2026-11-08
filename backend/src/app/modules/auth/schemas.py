"""The auth operations' request and response bodies; the contract is generated from them (app.main.build_openapi).
A model's docstring and its fields' descriptions are the contract's text."""

import uuid
from typing import Annotated, Literal

from pydantic import MISSING, AwareDatetime, BaseModel, ConfigDict, Field

from app.modules.auth.service.passwords import PASSWORD_MIN_LENGTH

_PASSWORD_MAX_LENGTH = 128
_NEXT_CURSOR = Field(description="Cursor for the next page; null on the last page.")


class User(BaseModel):
    id: uuid.UUID
    account: Annotated[str, Field(description="Lowercase; accounts are unique regardless of case.")]
    role: Literal["admin", "user"]
    is_disabled: bool
    created_at: AwareDatetime


class UserPage(BaseModel):
    users: list[User]
    next_cursor: Annotated[str | None, _NEXT_CURSOR]


class Session(BaseModel):
    """A signed-in session. The token is only in the cookie and never part of the resource."""

    id: uuid.UUID
    user: User
    created_at: AwareDatetime
    last_used_at: Annotated[
        AwareDatetime,
        Field(
            description="Updated at most once a minute. A session unused for the idle limit ends before `expires_at`."
        ),
    ]
    expires_at: Annotated[AwareDatetime, Field(description="The absolute end of the session, however much it is used.")]
    current: Annotated[bool, Field(description="Whether this is the session the request carries.")]


class SessionPage(BaseModel):
    sessions: list[Session]
    next_cursor: Annotated[str | None, _NEXT_CURSOR]


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: Annotated[
        str, Field(min_length=3, max_length=50, pattern="^[A-Za-z0-9_.-]+$", description="Stored lowercase.")
    ]
    password: Annotated[
        str,
        Field(
            min_length=PASSWORD_MIN_LENGTH,
            max_length=_PASSWORD_MAX_LENGTH,
            description=f"{PASSWORD_MIN_LENGTH} to {_PASSWORD_MAX_LENGTH} characters (code points), no composition "
            "rules (NIST SP 800-63B-4). The minimum also applies after NFKC normalization, which can shorten a "
            f"password (`e` plus a combining acute becomes `é`): a password under {PASSWORD_MIN_LENGTH} characters "
            "after it is rejected with code `too_short`. A password on the common or breached password list is "
            "rejected with code `password_too_common`.",
        ),
    ]


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: Annotated[str, Field(min_length=1, max_length=50, description="Case-insensitive.")]
    password: Annotated[str, Field(min_length=1, max_length=_PASSWORD_MAX_LENGTH)]


class SignInLock(BaseModel):
    locked: Annotated[
        bool, Field(description="Sign-ins from clients without a device cookie for the account are refused.")
    ]
    failures: Annotated[
        int,
        Field(
            ge=0,
            description="Failed sign-ins from such clients since the last successful one; the lock starts at 100.",
        ),
    ]


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_disabled: bool | MISSING = MISSING
