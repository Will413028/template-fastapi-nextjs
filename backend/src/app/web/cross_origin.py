"""CSRF protection: a state-changing request from another site is refused (Go version: net/http's
CrossOriginProtection, docs/adr/2026-10-18-api-and-auth-baseline.md D5).

Browsers say where a request comes from in Sec-Fetch-Site; same-origin and none (the address bar, a bookmark) pass,
anything else is refused. A browser too old to send it still sends Origin, which must then be the site's origin
(APP_ORIGIN): the Go version compares it with Host, but the Host the backend receives is not the name the browser used
(ADR vercel-rewrite-and-cloudflare-tunnel). A request with neither header is not from a browser (curl, the frontend's
server-side calls) and passes; the session cookie's SameSite=Lax is the other layer. GET, HEAD and OPTIONS change
nothing and are not checked.
"""

from typing import TYPE_CHECKING

from app.web.problems import FORBIDDEN, problem_response

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

CODE_CROSS_ORIGIN = "cross_origin_request"

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_ALLOWED_SITES = frozenset({b"same-origin", b"none"})


class CrossOriginMiddleware:
    def __init__(self, app: ASGIApp, app_origin: str) -> None:
        self.app = app
        self.app_origin = app_origin.encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in _SAFE_METHODS and self._cross_origin(scope):
            response = problem_response(FORBIDDEN, "cross-origin request rejected", CODE_CROSS_ORIGIN)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _cross_origin(self, scope: Scope) -> bool:
        site: bytes | None = None
        origin: bytes | None = None
        for name, value in scope["headers"]:
            if name == b"sec-fetch-site":
                site = value
            elif name == b"origin":
                origin = value
        if site is not None:
            return site not in _ALLOWED_SITES
        return origin is not None and origin != self.app_origin
