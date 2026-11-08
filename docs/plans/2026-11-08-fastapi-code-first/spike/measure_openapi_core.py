"""Step 1 (e): does openapi-core validate responses against FastAPI's 3.1 spec, and does it catch what D5 relies on
(an undeclared status, a problem body that breaks the Problem schema, a success body that breaks its model)?

    backend/.venv/bin/python docs/plans/2026-11-08-fastapi-code-first/spike/measure_openapi_core.py
"""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from openapi_core import Config, OpenAPI
from starlette.datastructures import Headers, QueryParams

import proto

spec_dict = proto.build(proto.OrderedRoute, fix_problem_media_type=True).openapi()
print("openapi:", spec_dict["openapi"])
# as app.web.contract does: openapi-core reads +json media types other than application/json as raw bytes otherwise
config = Config(extra_media_type_deserializers={proto.PROBLEM: lambda value, **_: json.loads(value)})
spec = OpenAPI.from_dict(spec_dict, config=config)


@dataclass
class Req:
    method: str
    path: str
    body: bytes | None = None
    content_type: str = "application/json"
    host_url: str = "http://backend"
    full_url_pattern: str = ""
    parameters: object = None
    headers: Headers = field(default_factory=lambda: Headers({}))
    query: QueryParams = field(default_factory=QueryParams)

    def __post_init__(self) -> None:
        self.full_url_pattern = self.host_url + self.path


@dataclass
class Resp:
    status_code: int
    data: bytes
    content_type: str
    headers: Headers = field(default_factory=lambda: Headers({}))


ITEM = {"id": "00000000-0000-0000-0000-000000000001", "title": "a", "description": None}
CASES = [
    ("201 valid Item", 201, ITEM, "application/json", True),
    ("201 Item without title", 201, {k: v for k, v in ITEM.items() if k != "title"}, "application/json", False),
    (
        "401 valid problem",
        401,
        {"type": "/problems/unauthenticated", "title": "u", "status": 401, "code": "session_invalid"},
        proto.PROBLEM,
        True,
    ),
    ("401 problem without status", 401, {"type": "/problems/unauthenticated", "title": "u"}, proto.PROBLEM, False),
    ("401 problem as application/json", 401, {"type": "/x", "title": "u", "status": 401}, "application/json", False),
    ("500 undeclared status", 500, {"type": "/problems/internal", "title": "i", "status": 500}, proto.PROBLEM, False),
    (
        "401 code outside the list (extensible)",
        401,
        {"type": "/x", "title": "u", "status": 401, "code": "new_code"},
        proto.PROBLEM,
        True,
    ),
]

failures = 0
for label, status, body, content_type, should_pass in CASES:
    request = Req("post", "/api/items", json.dumps({"title": "a"}).encode())
    response = Resp(status, json.dumps(body).encode(), content_type)
    try:
        spec.validate_response(request, response)
        outcome = "accepted"
    except Exception as error:  # report every kind openapi-core raises
        outcome = f"rejected: {type(error).__name__}: {str(error).splitlines()[0][:90]}"
    ok = outcome == "accepted" if should_pass else outcome != "accepted"
    failures += not ok
    print(f"{'ok ' if ok else 'BAD'}  {label:40} {outcome}")
print("unexpected outcomes:", failures)
sys.exit(1 if failures else 0)
