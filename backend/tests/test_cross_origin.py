"""CSRF protection (Go version: internal/httpapi/server_test.go TestCrossOriginPostRejected, net/http's
CrossOriginProtection).

A state-changing request is refused when the browser says it comes from another site (Sec-Fetch-Site), or, from a
browser too old to send Sec-Fetch-Site, when its Origin is not the site. The Go version compares Origin with Host;
here it is compared with APP_ORIGIN, because the Host the backend receives is not the name the browser used (D6).
"""

from typing import TYPE_CHECKING

import pytest
from fastapi import Request, Response

from apitest import assert_problem, bare_app, checked_client

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

SITE = "https://app.example"
WIDGET = {"name": "gear"}


def client() -> TestClient:
    app = bare_app(app_origin=SITE)

    @app.post("/api/widgets")
    async def create_widget(request: Request) -> Response:
        return Response(await request.body(), status_code=201)

    @app.get("/api/widgets")
    async def list_widgets() -> dict[str, list[object]]:
        return {"widgets": []}

    return checked_client(app)


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},  # a sibling subdomain is another origin
        {"Origin": "https://evil.example"},
        {"Origin": "http://app.example"},  # same host, other scheme
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site", "Origin": SITE},  # Sec-Fetch-Site decides when it is there
    ],
)
def test_cross_origin_post_rejected(headers: dict[str, str]) -> None:
    response = client().post("/api/widgets", json=WIDGET, headers=headers)
    body = assert_problem(response, 403, "/problems/forbidden")
    assert body["code"] == "cross_origin_request"


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "same-origin"},
        {"Sec-Fetch-Site": "none"},  # typed into the address bar or a bookmark
        {"Origin": SITE},  # a browser without Sec-Fetch-Site, from the site
        {},  # not a browser: curl, the frontend's server-side calls
    ],
)
def test_same_origin_or_non_browser_post_allowed(headers: dict[str, str]) -> None:
    assert client().post("/api/widgets", json=WIDGET, headers=headers).status_code == 201


def test_safe_methods_are_not_checked() -> None:
    assert client().get("/api/widgets", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
