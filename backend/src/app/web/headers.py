"""Security headers for every response."""

from typing import TYPE_CHECKING

from starlette.datastructures import MutableHeaders

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

# OWASP's REST Security Cheat Sheet for API responses a browser reads: the API is same-origin with cookies, so a
# browser does read them. frame-ancestors 'none' stops drag-and-drop clickjacking, and X-Frame-Options: DENY does the
# same for browsers without CSP Level 2. no-store also keeps Vercel's external rewrite from caching API responses
# (D6 in docs/plans/2026-11-03-fastapi-template.md). Permissions-Policy and X-XSS-Protection do nothing for JSON (the
# last one enables an XS-Leak); HSTS belongs to the edge that terminates TLS.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
}


class SecurityHeadersMiddleware:
    """Adds SECURITY_HEADERS that the response does not already set: an operation may still choose, say, its own
    Cache-Control, as the Go version allows by setting them before the handler runs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)
