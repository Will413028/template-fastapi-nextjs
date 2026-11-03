"""Builds the ASGI app. Module routers are included here as the modules land."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    # The contract is the hand-written api/openapi.yaml, so FastAPI's generated schema and docs stay off.
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    # Liveness: 200 while the process runs, without checking dependencies. Outside the API contract.
    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
