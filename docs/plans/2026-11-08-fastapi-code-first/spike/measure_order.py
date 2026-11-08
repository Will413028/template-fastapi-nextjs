"""Step 1 (a), (b) and the D6 facts: which error answers first, with FastAPI's own APIRoute and with OrderedRoute.

backend/.venv/bin/python docs/plans/2026-11-08-fastapi-code-first/spike/measure_order.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import proto

ITEM = "/api/items"
PATCH = "/api/items/00000000-0000-0000-0000-000000000001"
JSON = {"content-type": "application/json"}
USER = {"x-user": "u1"}

CASES = [
    # (label, method, path, headers, body)
    ("anon, bad JSON", "POST", ITEM, JSON, b"{bad"),
    ("anon, valid JSON, invalid body", "POST", ITEM, JSON, b"{}"),
    ("anon, text/plain", "POST", ITEM, {"content-type": "text/plain"}, b"x"),
    ("signed in, bad JSON", "POST", ITEM, JSON | USER, b"{bad"),
    ("signed in, trailing data", "POST", ITEM, JSON | USER, b'{"title":"a"} 1'),
    ("signed in, NaN", "POST", ITEM, JSON | USER, b'{"title":"a","description":NaN}'),
    ("signed in, top-level NaN", "POST", ITEM, JSON | USER, b"NaN"),
    ("signed in, non-UTF-8", "POST", ITEM, JSON | USER, b'{"title":"\xff"}'),
    ("signed in, no Content-Type", "POST", ITEM, USER, b'{"title":"a"}'),
    ("signed in, text/plain", "POST", ITEM, {"content-type": "text/plain"} | USER, b'{"title":"a"}'),
    (
        "signed in, json; charset=utf-8",
        "POST",
        ITEM,
        {"content-type": "application/json; charset=utf-8"} | USER,
        b'{"title":"a"}',
    ),
    ("signed in, {} (missing title)", "POST", ITEM, JSON | USER, b"{}"),
    ("signed in, PATCH application/json", "PATCH", PATCH, JSON | USER, b'{"title":"b"}'),
    ("signed in, PATCH merge-patch", "PATCH", PATCH, {"content-type": proto.MERGE_PATCH} | USER, b'{"title":"b"}'),
    (
        "signed in, PATCH merge-patch; charset",
        "PATCH",
        PATCH,
        {"content-type": proto.MERGE_PATCH + "; charset=utf-8"} | USER,
        b'{"title":"b"}',
    ),
    ("anon, PATCH merge-patch bad JSON", "PATCH", PATCH, {"content-type": proto.MERGE_PATCH}, b"{bad"),
    ("trailing slash /api/items/", "POST", ITEM + "/", JSON | USER, b'{"title":"a"}'),
]

# D6: a rate-limited public route; the counter says whether the limiter dependency ran (took a token)
LIMIT_CASES = [
    ("valid", b'{"account":"a","password":"p"}'),
    ("422 (missing field)", b'{"account":"a"}'),
    ("bad JSON", b"{bad"),
]


def run(route_class: type[APIRoute]) -> None:
    client = TestClient(proto.build(route_class))
    print(f"== route class: {route_class.__name__}")
    for label, method, path, headers, body in CASES:
        response = client.request(method, path, headers=headers, content=body, follow_redirects=False)
        detail = response.json().get("detail", "") if response.content else ""
        print(f"{response.status_code}  {label:42}  {detail}")
    for path, counter in (("/api/sessions", "plain"), ("/api/sessions-b", "with_body")):
        for label, body in LIMIT_CASES:
            before = proto.TOKENS[counter]
            response = client.post(path, headers=JSON, content=body)
            took = proto.TOKENS[counter] - before
            print(f"{response.status_code}  {path:16} {label:22} token taken: {took}")


run(APIRoute)
run(proto.OrderedRoute)
