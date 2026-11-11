# Add a backend module

`backend/src/app/modules/items/` is the worked example: copy its shape, not its fields. Names below: `<name>` is the
package and table (plural, snake_case, e.g. `widgets`), `<Entity>` the schema name (`Widget`), `<path>` the URL
segment (`widgets`). Read `AGENTS.md` "Backend layers", `docs/rules/api-design.md` and
`docs/rules/database-design.md` first; they are binding.

## 1. The bodies (`schemas.py`) and the contract

The contract, `backend/api/openapi.json`, is generated from the routes and the modules' `schemas.py` models.

- `schemas.py` (copy items'): `<Entity>` (every field required, nullable ones `X | None`), `<Entity>Page` (`<name>`
  list and `next_cursor`), `Create<Entity>Request` and `<Entity>Patch`, both with
  `model_config = REQUEST_CONFIG` (`app.http.schemas`; the route check refuses a body without it). Length and range
  limits go here (`Field(min_length=..., max_length=...)`), and again as a CHECK in the table. A PATCH field that may
  be left out is `X | MISSING = MISSING`. `REQUEST_CONFIG` is strict, so nothing is converted (`"true"` is not a
  boolean): a body field that JSON can only carry as a string (a UUID, a date-time) needs `Field(strict=False)`, or
  valid input answers 422.
- Run `make -C backend gen` (writes `backend/api/openapi.json`) and `pnpm -C frontend gen:api`.

## 2. The module (`backend/src/app/modules/<name>/`)

| File | Copy from items | Rules |
|---|---|---|
| `models.py` | `Item` | UUIDv7 `id`, `owner_id` as `ForeignKey("users.id", ondelete="CASCADE")` (a string, no ORM relationship), `created_at`/`updated_at` timestamptz with `NOW`, `text` + CHECK for lengths, and the `<name>_owner_id_id_idx` index on `(owner_id, id DESC)` for keyset pagination |
| `repository.py` | all five queries | SQLAlchemy Core only; every query that reads or changes an existing row has `<Table>.owner_id == owner_id` in its WHERE; `update` sets `updated_at=func.now()` |
| `service.py` | `ItemsService` | takes `Principal` from the router, owns the transaction (`engine.begin()` to write, `connect()` to read), raises `AppError(Kind.NOT_FOUND, ...)` for another user's row exactly as for a missing one; no `fastapi`/`starlette` |
| `dependencies.py` | `items_service` | reads the service from `app.state` under the module's own `STATE_KEY` |
| `router.py` | all routes | `APIRouter(prefix="/api", tags=[...], route_class=ApiRoute)`; each route has `operation_id`, `summary` and `responses=problems(...)` for what its own code sends (404, a cross-origin 403, 500; `ApiRoute` adds 400, 401, 413, 415, 422); every route takes `principal: SignedIn` (or carries `PUBLIC`); converts `MISSING` to the service's types (`is MISSING`, never `None` checks, so PATCH tells "not sent" from `null`); 201 sets `Location: /api/<path>/{id}`; no `sqlalchemy` imports |

Without a PATCH operation, leave out `Patch`, `update` and the update query.

## 3. Wiring

- `app/main.py`: add the service to `Modules`, include the router and set `app.state.<STATE_KEY>` where the
  items ones are.
- `app/cli/server.py`, `tests/authtest.py` (`auth_api`) and `tests/authfake.py` (`fake_modules`): construct it.
- `app/migrations/schema.py` `MODEL_MODULES`: add `app.modules.<name>.models` (`tests/test_schema.py` fails otherwise).
- `app.main` checks at startup that every `/api` route is an `ApiRoute` with one access marker and a body model with
  `REQUEST_CONFIG`: a route that breaks it stops the app.

## 4. Migration

`make generate-migration MSG=add_<name>` from the repository root (needs Docker, so not on a shared development
machine: ask, or let CI's database job confirm `alembic check`). Once a revision is on `main` it is immutable
(`scripts/check_migrations_immutable.py`); a new `NOT NULL` column on a table with rows needs a constant default.
squawk (`make -C backend squawk`) checks the SQL.

## 5. Tests (copy the three items files)

- `tests/test_<name>_repository.py`: every query builds, and every query on an existing row is scoped to the owner
  (no database; runs locally).
- `tests/test_<name>_http.py`: the refusals that need no database (contract limits, malformed id, `null` for a
  required field, wrong media type on PATCH, cross-site create) against `fake_app`; `checked_client` checks each
  response against the generated contract.
- `tests/test_<name>_db.py` (`pytestmark = pytest.mark.db`, CI only): create with `Location` and owner, list newest
  first and keyset paging that survives inserts, get, merge-patch semantics, `updated_at` changes, delete then 404,
  and another user's row is 404 for get, patch and delete and stays unchanged.
- `tests/test_queries_db.py` `LISTS`: add the list operation (its path with `limit=100`), so the N+1 check covers it;
  `tests/test_queries.py` fails until every GET with the `limit` parameter is there.

Update `scripts/quality-locks.json` (`backend`, `backend_db`) to the new test counts in the same commit.

## 6. Verify

`make -C backend check`, `pnpm -C frontend lint`, `pnpm -C frontend test`; push and wait for CI's database job.
Prove the owner scoping test can fail: in a throwaway copy of the repository (never the working tree), remove one
`owner_id` condition and run the repository test; it must fail.
