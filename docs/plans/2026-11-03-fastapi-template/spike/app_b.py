# Spike (b): spec-first — validate FastAPI requests/responses against the Go contract with openapi-core.
import sys, uuid, datetime
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from openapi_core import OpenAPI
from openapi_core.contrib.starlette.middlewares import StarletteOpenAPIMiddleware

spec = OpenAPI.from_file_path(sys.argv[1])
app = FastAPI()
BAD = "--bad-response" in sys.argv

@app.post("/api/items", status_code=201)
async def create_item(request: Request):
    body = await request.json()
    now = datetime.datetime.now(datetime.UTC).isoformat()
    item = {"id": str(uuid.uuid4()), "title": body["title"], "description": body.get("description"),
            "owner_id": str(uuid.uuid4()), "created_at": now, "updated_at": now}
    if BAD:
        del item["owner_id"]
    return JSONResponse(item, status_code=201, headers={"Location": f"/api/items/{item['id']}"})

app.add_middleware(StarletteOpenAPIMiddleware, openapi=spec)
c = TestClient(app, raise_server_exceptions=False)
for name, payload in [("ok", {"title": "a"}), ("title too long", {"title": "x" * 201}),
                      ("extra field", {"title": "a", "nope": 1}), ("empty title", {"title": ""})]:
    r = c.post("/api/items", json=payload, cookies={"__Host-session": "x"})
    print(f"{name:15} -> {r.status_code} {r.text[:160]}")
