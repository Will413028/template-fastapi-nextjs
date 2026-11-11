"""Step 1 (c), (d) and the first half of (f): what app.openapi() writes for MISSING fields, problem responses, and
x-extensible-enum. Writes the spec to the path given (default: stdout only).

    backend/.venv/bin/python docs/plans/2026-11-08-fastapi-code-first/spike/measure_schema.py <out.json>
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import proto

spec = proto.build(proto.OrderedRoute).openapi()
schemas = spec["components"]["schemas"]

print("openapi:", spec["openapi"])
print("paths:", sorted(spec["paths"]))
print("\n(c) ItemPatch:", json.dumps(schemas["ItemPatch"], indent=1))
print("\n(c)(f) Problem:", json.dumps(schemas["Problem"], indent=1))
print("\n(d) POST /api/items responses:", json.dumps(spec["paths"]["/api/items"]["post"]["responses"], indent=1))
print("\n(d) PATCH requestBody:", json.dumps(spec["paths"]["/api/items/{id}"]["patch"]["requestBody"], indent=1))
print("\n(d) leftover FastAPI schemas:", sorted(k for k in schemas if "Validation" in k))

fixed = proto.build(proto.OrderedRoute, fix_problem_media_type=True).openapi()
print(
    "\n(d) with problem_media_type_only, POST /api/items 401:",
    json.dumps(fixed["paths"]["/api/items"]["post"]["responses"]["401"], indent=1),
)

if len(sys.argv) > 1:
    Path(sys.argv[1]).write_text(json.dumps(fixed, indent=2) + "\n")
