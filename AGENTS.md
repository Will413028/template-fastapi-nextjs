# template-fastapi-nextjs

Fullstack monorepo template with a FastAPI backend and a Next.js frontend. The frontend deploys to Vercel, which rewrites `/api/*` to the backend on one origin; the backend and PostgreSQL run with Docker Compose on a VM and are reached through a Cloudflare Tunnel.

Status: modules in progress. The backend serves `GET /health`, `GET /health/ready` and seven of the contract's operations (sign-up, sign-in, sign-out, the current user and session, reading a user and a session), with the `serve`, `migrate` and `healthcheck` commands; the operations not routed yet are listed in `backend/src/app/main/routes.py` `UNIMPLEMENTED`, and each module adds its own.

## Verification

- Branch model: daily work goes to `dev`; `main` changes only through a `dev`→`main` PR merged with a merge commit (no squash, no rebase); never push to `main` directly.
- Backend tests use `pytest`, frontend tests use `vitest`; a task is complete only when `make -C backend check` (backend) and `pnpm test` (frontend) pass, and CI's backend job passes when the change touches the database. `make -C backend check` runs what CI's backend job runs except the database tests: `uv lock --check`, `gen-check` (the Pydantic models in `backend/src/app/web/contract_models.py` match `backend/api/openapi.yaml`), `ruff format --check`, `ruff check`, the import-linter contracts (`lint-imports`), `pyright` in strict mode, `vulture`, `pytest -m "not db"`, squawk on every revision's SQL (needs Node; `backend/tools/package.json`) and the revision immutability check.
- Tests that need PostgreSQL carry `@pytest.mark.db` (a test using the `postgres` fixture without it fails collection) and start a container with testcontainers (`backend/tests/dbtest.py`). They run only in CI (`make -C backend test-db`); do not run them, or `make migration`, on a shared development machine.
- pytest turns every warning into an error (`backend/pytest.ini`): fix a deprecation when an upgrade brings it, do not filter it.
- `make lint-docker` (hadolint, pinned in the root `Makefile`) checks both Dockerfiles; every finding fails, infos included.
- `git push` runs CI's checks first through the lefthook pre-push hook (all but the compose stack job, except its `make lint-docker`); it needs `uv`, `actionlint`, `zizmor`, `gitleaks` and `osv-scanner` installed. Do not skip it with `--no-verify`.
- Suppression comments (`# noqa`, `# ruff: noqa`, `# ruff: ignore`, `# type: ignore`, `# pyright: ignore`, `squawk-ignore`, `biome-ignore`, `@ts-expect-error`, `@ts-ignore`, `eslint-disable`) and the number of tests run (frontend and backend) are locked in `scripts/quality-locks.json`; `scripts/check_locks.py` (CI and pre-push) fails when a count differs. Suppressions only go down and tests only up: update the lock in the same commit. Fix the code instead of suppressing: the template ships with zero suppressions.
- A commit that edits a checker's configuration (`backend/ruff.toml`, `backend/pyrightconfig.json`, `backend/.importlinter`, `backend/.squawk.toml`, `backend/pytest.ini`, `biome.json`, `.dependency-cruiser.cjs`, `knip.config.ts`, `tsconfig.json`, any `osv-scanner.toml`, the lock scripts) or moves a lock the bad way carries a `Quality-Gate: <why>` trailer, and a commit over 1,000 changed lines (files `.gitattributes` marks `linguist-generated` not counted) is split or carries a `Large-Change: <why>` trailer; `scripts/check_commit_policy.py` checks both at commit time (lefthook) and for every pushed commit (CI and pre-push).
- Frontend tests run in random order (vitest `sequence.shuffle`); a failure prints its seed, and `--sequence.seed=<seed>` replays it.
- A bug fix starts with a test that fails because of the bug; the fix makes it pass, and the commit keeps the test.
- Cover observable behavior, failure conditions, and regressions; do not add tests per module or function when existing tests already cover the behavior. Docs-only or formatting changes need only the relevant static checks.

## Backend layers

The backend is a modular monolith organized by feature. `backend/.importlinter` enforces these rules in CI and at pre-push; a broken contract prints how to fix it. Move code instead of adding `ignore_imports`.

| Package | Holds | May import |
|---|---|---|
| `app.main` | app factory, middleware assembly, CLI, migrations environment | everything below |
| `app.modules.<name>` | one feature: `router.py`, `schemas.py`, `dependencies.py` (HTTP: routes, FastAPI dependencies, and any ASGI middleware `app.main.factory` mounts for the module); `service.py`, or a `service/` package when it grows (business rules, owns the transaction); optional `repository.py` (complex queries); `models.py` (tables) | `app.web`, `app.platform`; never another module |
| `app.web` | HTTP helpers shared by routers (problem responses, request ids, rate limits, headers) | `app.platform` |
| `app.platform` | settings, database session, pagination, clock, the authenticated principal | third-party packages only; no `fastapi` or `starlette` |

- Inside a module the order is router > service > repository > models; every file in a module belongs to one of those layers.
- `service`, `repository` and `models` do not import `fastapi` or `starlette`, directly or through another module; routers do not import `sqlalchemy`, a repository, models or `app.platform.db` themselves (they call the service, which does).
- Every `/api` route carries exactly one access marker from `app.web.access` (`PUBLIC`, `SIGNED_IN`) and matches a contract operation (method, path, path parameter names); `app.main.routes` checks both at startup.
- Modules share nothing directly: what one needs from another goes through `app.platform` (for example the principal auth's middleware sets), and a table refers to another module's table by a string foreign key such as `"users.id"`, without an ORM relationship.
- Only `app.platform.config` reads environment variables (ruff `TID251`).
- No import cycles between sibling packages (`acyclic_siblings`).

## Constraints

- `make down-v` deletes the Postgres volume and all local data.
- Never commit `.env` or `.secrets/`; add new variables to `.env-example`.
- Agents do not add or upgrade dependencies on their own: list each package and version, confirm the package exists and was not registered recently (PyPI and deps.dev show publish dates), and wait for approval before installing. Backend versions are pinned exactly in `backend/pyproject.toml`; uv ignores releases younger than 7 days (`exclude-newer`).
- Fix known vulnerabilities (`osv-scanner` over every lockfile, `pnpm audit`, Dependabot alerts; the Security workflow rescans the default branch `dev`, which contains all of `main`, every Monday) by severity: critical within 7 days, high within 30 days, medium within 90 days, low at the next routine upgrade. osv-scanner blocks any finding; an advisory that cannot be fixed at once goes into an `osv-scanner.toml` next to that lockfile as `[[IgnoredVulns]]` with `ignoreUntil` set to its deadline and a `reason`, so the scan fails again when the deadline passes. Dev tools are dependencies too: they are locked in `backend/uv.lock` with the runtime packages.

## Monorepo Structure

```
├── backend/                # FastAPI (modular monolith, uv), contract in backend/api/openapi.yaml
├── frontend/               # Next.js 16 (App Router, TypeScript, Tailwind, shadcn/ui); deployed to Vercel
├── deploy/                 # PostgreSQL initdb scripts (DB roles)
├── docker-compose.yml      # VM stack (postgres + backend; cloudflared with --profile tunnel)
├── docker-compose.dev.yml  # Dev overrides (compose watch, frontend dev server on 127.0.0.1:3000)
├── Makefile                # Docker Compose shortcuts
├── scripts/                # Repo tooling (init_env, quality locks, commit policy)
└── .env-example            # Environment variable template
```

## Quick Reference

```bash
# Docker Compose (from root)
make init-env                       # Create .env and the database password files in .secrets/
make dev                            # Start dev stack (compose watch) on http://localhost:3000
make up                             # Start the VM stack without the tunnel (detached)
make up-prod                        # Start the VM stack with the Cloudflare Tunnel (MODE=prod; needs .secrets/tunnel_token)
make down                           # Stop stack
make down-v                         # Stop stack and remove volumes
make migration                      # Apply the revisions to the stack's database (app migrate)
make generate-migration MSG=x       # Generate a revision from the models (needs Docker)
make build                          # Build Docker images
make status                         # Show running containers
make logs                           # Tail all logs
make logs-backend                   # Tail backend logs
make lint-docker                    # hadolint over both Dockerfiles

# Backend development (from backend/)
make sync             # Install the locked dependencies into .venv
make run              # Start the API server with MODE=dev
make test             # pytest without the db tests
make test-db          # The db tests (needs Docker; CI runs them)
make check            # CI's backend job, minus the db tests
make gen              # Regenerate src/app/web/contract_models.py from api/openapi.yaml (gen-check fails when stale)
make migration MSG=x  # Generate an Alembic revision from the models (needs Docker)
make squawk           # Lock and compatibility checks on every revision's SQL (needs Node)
make fmt              # ruff format and safe fixes

# Frontend development (from frontend/)
pnpm dev              # Start Next.js dev server (Turbopack)
pnpm build            # Production build
pnpm lint             # TypeScript check + Biome lint
pnpm format           # Biome format
pnpm gen:api          # Regenerate API types from backend/api/openapi.yaml
```

## Architecture

- Frontend (Next.js App Router, feature-based, next-intl, generated API client): `frontend/AGENTS.md`.
- Decisions: `docs/adr/`. Some documents under `docs/` still describe the Go template's Caddy deployment; they are being rewritten.
- `docker-compose.yml` runs what the VM runs and publishes no port. `docker-compose.dev.yml` adds the frontend dev server on `127.0.0.1:3000`, whose development rewrite forwards `/api` to the backend.

## Design Rules

開發新功能前，必須先閱讀對應的設計規則。這些規則是強制性的，所有程式碼必須遵守：

- **Database 設計**：`docs/rules/database-design.md` — 命名規範、schema 與型別、查詢、Migration
- **API 設計**：`docs/rules/api-design.md` — URL 設計、回應與錯誤格式、分頁、契約與認證規範
- **認證的威脅建模**：`docs/security/threat-model-auth.md` — 改認證、session、角色或 operation 的權限時，在同一個改動裡更新
- **個資盤點**：`docs/privacy/data-inventory.md` — 新增存放個資的欄位、日誌欄位或外部處理者時，在同一個改動裡更新

## Conventions

- Backend is Python 3.14 managed with `uv` (`backend/pyproject.toml`, `backend/uv.lock`); frontend uses `pnpm`.
- The HTTP contract is the hand-written `backend/api/openapi.yaml`; FastAPI's generated schema is off; the backend generates its Pydantic models (`make -C backend gen`) and the frontend its types (`pnpm gen:api`) from the contract.
