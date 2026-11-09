"""The route class every /api route uses (docs/rules/api-design.md): before FastAPI reads the body,
it refuses a request in this order:

1. access (app.http.access): no valid session 401, a role that may not call the operation 403;
2. the rate limit (app.http.ratelimit), which takes a token from every request that gets this far;
3. a Content-Type the operation does not accept 415 (a PATCH's carries Accept-Patch, RFC 5789);
4. a body that is not exactly one UTF-8 JSON value without NaN or Infinity 400 (RFC 8259).

What is left, parameters and a body against their models, FastAPI checks and app.http.errors turns into 422. Before all
of it, app.http.body_limit refuses a body over the limit (413, before routing, so for an unknown path too) and
Starlette's routing an unknown path or method (404, 405), so the order is 413 > 404/405 > 401/403 > 429 > 415 > 400 >
422. Spring Security, ASP.NET Core and DRF also
check who is calling before they parse what was sent: an anonymous caller learns nothing about the request it would
have to send.

The route also declares in the contract each problem status it and app.http.body_limit answer, so a route's
`responses=` lists only what its own code sends.
"""

import email.message
import json
from typing import TYPE_CHECKING, Any, NoReturn, override

from fastapi import Request
from fastapi.dependencies.utils import get_flat_params
from fastapi.params import Body
from fastapi.routing import APIRoute

from app.http.access import admin, signed_in
from app.http.problems import BAD_REQUEST, UNSUPPORTED_MEDIA_TYPE, problem_response
from app.http.ratelimit import rate_limited, take_token
from app.http.schemas import MERGE_PATCH, problems

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterable

    from fastapi.dependencies.models import Dependant
    from pydantic.fields import FieldInfo
    from starlette.responses import Response


class _NotJSONError(ValueError):
    pass


def _reject_constant(name: str) -> NoReturn:
    msg = f"{name} is not JSON"
    raise _NotJSONError(msg)


class StrictJSONRequest(Request):
    """A request whose json() is exactly one UTF-8 JSON value; Python's json reads NaN and Infinity, which JSON does
    not have. FastAPI's handler calls json() and gets the value ApiRoute already parsed (FastAPI documents a custom
    Request in a custom route class as the way to change how a body is read)."""

    _strict_json: Any

    @override
    async def json(self) -> Any:
        if not hasattr(self, "_strict_json"):
            # UnicodeDecodeError and JSONDecodeError are ValueErrors too
            self._strict_json = json.loads((await self.body()).decode(), parse_constant=_reject_constant)
        return self._strict_json


def dependency_calls(dependencies: Iterable[Dependant]) -> Iterable[Callable[..., object]]:
    """Every callable among dependencies and their own dependencies: where ApiRoute and app.main look for markers."""
    for dependency in dependencies:
        if dependency.call is not None:
            yield dependency.call
        yield from dependency_calls(dependency.dependencies)


def _declared_media_type(field: FieldInfo) -> str:
    """The media type a body parameter declares: FastAPI makes every body field a Body, whose media_type defaults to
    application/json."""
    if not isinstance(field, Body):
        msg = f"a body field is a fastapi.params.Body, not {type(field).__name__}"
        raise TypeError(msg)
    return field.media_type


def _media_type(content_type: str) -> str:
    # type/subtype in lowercase without parameters (`; charset=utf-8` does not matter), as FastAPI reads it
    message = email.message.Message()
    message["content-type"] = content_type
    return message.get_content_type()


class ApiRoute(APIRoute):
    """The route class of every /api route (`APIRouter(route_class=ApiRoute)`; app.main checks that each route is
    one)."""

    @override
    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        super().__init__(path, endpoint, **kwargs)
        # The statuses this route answers depend on its markers and body, which FastAPI works out while it builds the
        # route; built again with them, FastAPI turns each into the contract's schema as for any declared response.
        declared: dict[int | str, dict[str, Any]] = kwargs.get("responses") or {}
        added = {status: response for status, response in self._checked().items() if status not in declared}
        if added:
            kwargs["responses"] = dict(sorted({**declared, **added}.items(), key=lambda item: str(item[0])))
            super().__init__(path, endpoint, **kwargs)

    def _checked(self) -> dict[int | str, dict[str, Any]]:
        """The problem responses of what this route checks."""
        calls = set(dependency_calls(self.dependant.dependencies))
        statuses: set[int] = set()
        if calls & {signed_in, admin}:
            statuses.add(401)
        if admin in calls:
            statuses.add(403)
        if rate_limited in calls:
            statuses.add(429)
        if self.body_field is not None:
            statuses |= {400, 413, 415}
        # FastAPI adds its own HTTPValidationError schema to an operation with input that declares no 422
        if self.body_field is not None or get_flat_params(self.dependant):
            statuses.add(422)
        return problems(*sorted(statuses))

    @override
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()
        calls = set(dependency_calls(self.dependant.dependencies))
        needs_admin = admin in calls
        needs_session = signed_in in calls
        limited = rate_limited in calls
        # Body(media_type=...) on the body parameter, application/json by default
        media_type = None if self.body_field is None else _declared_media_type(self.body_field.field_info)
        accept_patch = {"Accept-Patch": MERGE_PATCH} if "PATCH" in (self.methods or ()) else None
        # what FastAPI parses as JSON: application/json and any application/*+json
        json_body = media_type is not None and (media_type == "application/json" or media_type.endswith("+json"))

        async def checked(request: Request) -> Response:
            request = StrictJSONRequest(request.scope, request.receive)
            # The markers' own checks, run first; as the route's dependencies they run again, finding nothing to refuse.
            if needs_admin:
                await admin(request)
            elif needs_session:
                await signed_in()
            if limited:
                take_token(request)
            # an empty body has no media type to check: FastAPI answers 422 `required` for it
            if media_type is not None and await request.body():
                # RFC 9110: a body without Content-Type may be treated as application/octet-stream
                if _media_type(request.headers.get("content-type", "application/octet-stream")) != media_type:
                    # the accepted type, never the request's own header
                    return problem_response(
                        UNSUPPORTED_MEDIA_TYPE, f"accepted Content-Type: {media_type}", headers=accept_patch
                    )
                if json_body:
                    try:
                        await request.json()
                    except ValueError:
                        return problem_response(BAD_REQUEST, "request body is not valid JSON")
            return await handler(request)

        return checked
