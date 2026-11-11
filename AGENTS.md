# template-fastapi-nextjs

Fullstack monorepo template with a FastAPI backend and a Next.js frontend. The frontend deploys to Vercel, which rewrites `/api/*` to the backend on one origin; the backend and PostgreSQL run with Docker Compose on a VM and are reached through a Cloudflare Tunnel.

<!-- >>> template-only -->
This repository is a template: `make new NAME=x DEST=<dir>` generates a project from it (`template/new_project.py`), dropping `template/`, `docs/plans/` and every block between template-only markers (the comment lines that open and close this paragraph), and replacing `README.md` with `template/derived/README.md`. A line that only makes sense in the template goes inside such a block; CI's generate job runs the checks that need no database on a generated project.
<!-- <<< template-only -->

The backend serves `GET /health`, `GET /health/ready` and every operation of the contract (accounts, sessions, the admin's user management and sign-in locks, and the items CRUD), with the `serve`, `migrate`, `healthcheck`, `seed-admin` and `recover-admin` commands (`make seed` creates the admin, `make recover-admin` gives an existing admin a new password); `backend/src/app/main.py` refuses to start when a route breaks the route check (`check_routes`).

## Verification

- Branch model: daily work goes to `dev`; `main` changes only through a `dev`→`main` PR merged with a merge commit (no squash, no rebase); never push to `main` directly.
- Backend tests use `pytest`, frontend tests use `vitest`; a task is complete only when `make -C backend check` (backend) and `pnpm test` (frontend) pass, and CI's backend job passes when the change touches the database. `make -C backend check` runs what CI's backend job runs except the database tests: `uv lock --check`, `gen-check` (`backend/api/openapi.json` matches the contract generated from the routes and their `schemas.py` models), `ruff format --check`, `ruff check`, the import-linter contracts (`lint-imports`), `pyright` in strict mode, `vulture`, `pytest -m "not db"`, squawk on every revision's SQL (needs Node; `backend/tools/package.json`), the revision immutability check and `api-breaking` (oasdiff: no breaking change to `backend/api/openapi.json` since `BASE`; downloads oasdiff into `backend/bin/` on first use). Locally `BASE` defaults to `origin/dev` (`origin/main` in a clone without it); CI passes the pull request's base branch, or a push's previous tip.
- Tests that need PostgreSQL carry `@pytest.mark.db` (a test using the `postgres` fixture without it fails collection) and start a container with testcontainers (`backend/tests/dbtest.py`). They run only in CI (`make -C backend test-db`); do not run them, or `make migration`, on a shared development machine.
- Coverage is reported, not gated: CI's backend job writes the total line coverage of both pytest runs and, on a pull request, the coverage of the changed lines (`make -C backend coverage COVER_BASE=<ref>` after `make test`) to the run summary.
- pytest turns every warning into an error (`backend/pytest.ini`): fix a deprecation when an upgrade brings it, do not filter it.
- `make lint-docker` (hadolint, pinned in the root `Makefile`) checks both Dockerfiles; every finding fails, infos included.
- Release binaries are pinned with a sha256 per platform: hadolint in the root `Makefile`, oasdiff (OpenAPI diff) in `backend/Makefile` (`make -C backend tools` downloads it into `backend/bin/`); `scripts/tool_versions.py` verifies the pins (CI and pre-push) and `bump <tool>` updates them.
- `git push` runs CI's checks first through the lefthook pre-push hook (all but the compose stack job, except its `make lint-docker`); it needs `uv`, `actionlint`, `zizmor`, `gitleaks` and `osv-scanner` installed. Do not skip it with `--no-verify`.
- Suppression comments (`# noqa`, `# ruff: noqa`, `# ruff: ignore`, `# type: ignore`, `# pyright: ignore`, `squawk-ignore`, `biome-ignore`, `@ts-expect-error`, `@ts-ignore`, `eslint-disable`) and the number of tests run (frontend and backend) are locked in `scripts/quality-locks.json`; `scripts/check_locks.py` (CI and pre-push) fails when a count differs. Suppressions only go down and tests only up: update the lock in the same commit. Fix the code instead of suppressing: the template ships with zero suppressions.
- A commit that edits a checker's configuration (`backend/ruff.toml`, `backend/pyrightconfig.json`, `backend/.importlinter`, `backend/.squawk.toml`, `backend/pytest.ini`, `biome.json`, `.dependency-cruiser.cjs`, `knip.config.ts`, `tsconfig.json`, any `osv-scanner.toml`, `backend/api/breaking-ignore.txt`, the lock scripts) or moves a lock the bad way carries a `Quality-Gate: <why>` trailer, and a commit over 1,000 changed lines (files `.gitattributes` marks `linguist-generated` not counted) is split or carries a `Large-Change: <why>` trailer; `scripts/check_commit_policy.py` checks both at commit time (lefthook) and for every pushed commit (CI and pre-push).
- Frontend tests run in random order (vitest `sequence.shuffle`); a failure prints its seed, and `--sequence.seed=<seed>` replays it.
- A bug fix starts with a test that fails because of the bug; the fix makes it pass, and the commit keeps the test.
- Cover observable behavior, failure conditions, and regressions; do not add tests per module or function when existing tests already cover the behavior. Docs-only or formatting changes need only the relevant static checks.

## Backend layers

The backend is a modular monolith organized by feature. `backend/.importlinter` enforces these rules in CI and at pre-push; a broken contract prints how to fix it. Move code instead of adding `ignore_imports`.

| Package | Holds | May import |
|---|---|---|
| `app.cli` | the `app` command: the dispatcher imports each subcommand's module (`serve`, `migrate`, `healthcheck`, `seed-admin`, `recover-admin`, `openapi`) only when it runs; `logs.py` is the logging setup `serve` uses | everything below |
| `app.main` | app factory and middleware assembly (`create_app`) | everything below except `app.migrations` |
| `app.migrations` | Alembic environment, revisions and the schema metadata | everything below except `app.main` |
| `app.modules.<name>` | one feature: `router.py`, `schemas.py`, `dependencies.py` (HTTP: routes, FastAPI dependencies, and any ASGI middleware `app.main` mounts for the module); `service.py`, or a `service/` package when it grows (business rules, owns the transaction); optional `repository.py` (complex queries); `models.py` (tables) | `app.http`, `app.core`; never another module |
| `app.http` | HTTP helpers shared by routers (the `ApiRoute` route class, problem responses, request ids, rate limits, headers) | `app.core` |
| `app.core` | settings, database session, pagination, clock, the authenticated principal | third-party packages only; no `fastapi` or `starlette` |

- Inside a module the order is router > service > repository > models; every file in a module belongs to one of those layers.
- `service`, `repository` and `models` do not import `fastapi` or `starlette`, directly or through another module; routers do not import `sqlalchemy`, a repository, models or `app.core.db` themselves (they call the service, which does).
- Every `/api` route is an `app.http.route.ApiRoute` (it checks access, the rate limit, the media type and the JSON before FastAPI reads the body), carries exactly one access marker from `app.http.access` (`PUBLIC`, `SIGNED_IN`, `ADMIN`), and takes a body model with `app.http.schemas.REQUEST_CONFIG`; `app.main` checks all three at startup.
- Modules share nothing directly: what one needs from another goes through `app.core` (for example the principal auth's middleware sets), and a table refers to another module's table by a string foreign key such as `"users.id"`, without an ORM relationship.
- Only `app.core.config` reads environment variables (ruff `TID251`).
- No import cycles between sibling packages (`acyclic_siblings`).

## Constraints

- `make down-v` deletes the Postgres volume and all local data.
- Never commit `.env` or `.secrets/`; add new variables to `.env-example`.
- Agents do not add or upgrade dependencies on their own: list each package and version, confirm the package exists and was not registered recently (PyPI and deps.dev show publish dates), and wait for approval before installing. Backend versions are pinned exactly in `backend/pyproject.toml`; uv ignores releases younger than 7 days (`exclude-newer`).
- Fix known vulnerabilities (`osv-scanner` over every lockfile, `pnpm audit`, Dependabot alerts; the Security workflow rescans the default branch `dev`, which contains all of `main`, every Monday) by severity: critical within 7 days, high within 30 days, medium within 90 days, low at the next routine upgrade. osv-scanner blocks any finding; an advisory that cannot be fixed at once goes into an `osv-scanner.toml` next to that lockfile as `[[IgnoredVulns]]` with `ignoreUntil` set to its deadline and a `reason`, so the scan fails again when the deadline passes. Dev tools are dependencies too: they are locked in `backend/uv.lock` with the runtime packages.

## Monorepo Structure

```
├── backend/                # FastAPI (modular monolith, uv), contract generated into backend/api/openapi.json
├── frontend/               # Next.js 16 (App Router, TypeScript, Tailwind, shadcn/ui); deployed to Vercel
├── deploy/                 # PostgreSQL initdb scripts (DB roles)
├── docker-compose.yml      # VM stack (postgres + backend; cloudflared with --profile tunnel)
├── docker-compose.dev.yml  # Dev overrides (compose watch, frontend dev server on 127.0.0.1:3000)
├── Makefile                # Docker Compose shortcuts
├── scripts/                # Repo tooling (init_env, quality locks, commit policy)
# >>> template-only
├── template/               # make new: the generator, its tests, files that replace the template's in a new project
# <<< template-only
└── .env-example            # Environment variable template
```

## Quick Reference

```bash
# Docker Compose (from root)
# >>> template-only
make new NAME=my-project DEST=../my-project  # Generate a new project (names, database, roles, image, brand; REPO=owner/repo)
# <<< template-only
make init-env                       # Create .env and the database password files in .secrets/
make dev                            # Start dev stack (compose watch) on http://localhost:3000
make up                             # Start the VM stack without the tunnel (detached)
make up-prod                        # Start the VM stack with the Cloudflare Tunnel (MODE=prod; needs .secrets/tunnel_token)
make down                           # Stop stack
make down-v                         # Stop stack and remove volumes
make seed ADMIN_ACCOUNT=admin       # Create the admin account in the running stack (password from the terminal or stdin)
make recover-admin ADMIN_ACCOUNT=admin  # Give an existing admin a new password, enable it, sign it out everywhere
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
make gen              # Regenerate api/openapi.json from the routes (app openapi; gen-check fails when stale)
make api-breaking     # Fail on a breaking change to api/openapi.json since BASE (default origin/dev, else origin/main; api/breaking-ignore.txt)
make migration MSG=x  # Generate an Alembic revision from the models (needs Docker)
make squawk           # Lock and compatibility checks on every revision's SQL (needs Node)
make tools            # Download the pinned release binaries (oasdiff) into bin/
make fmt              # ruff format and safe fixes

# Frontend development (from frontend/)
pnpm dev              # Start Next.js dev server (Turbopack)
pnpm build            # Production build
pnpm lint             # TypeScript check + Biome lint
pnpm format           # Biome format
pnpm gen:api          # Regenerate API types from backend/api/openapi.json
```

## Architecture

- Frontend (Next.js App Router, feature-based, next-intl, generated API client): `frontend/AGENTS.md`.
- Adding a backend module: `docs/rules/add-module.md` (the items module is the worked example).
- Decisions: `docs/adr/`. Deployment (Vercel, the VM, the Cloudflare Tunnel) and the first-deploy checklist: `docs/deploy-vps.md`.
- `docker-compose.yml` runs what the VM runs and publishes no port. `docker-compose.dev.yml` adds the frontend dev server on `127.0.0.1:3000`, whose development rewrite forwards `/api` to the backend.

## Design Rules

開發新功能前，必須先閱讀對應的設計規則。這些規則是強制性的，所有程式碼必須遵守：

- **Database 設計**：`docs/rules/database-design.md` — 命名規範、schema 與型別、查詢、Migration
- **API 設計**：`docs/rules/api-design.md` — URL 設計、回應與錯誤格式、分頁、契約與認證規範
- **認證的威脅建模**：`docs/security/threat-model-auth.md` — 改認證、session、角色或 operation 的權限時，在同一個改動裡更新
- **個資盤點**：`docs/privacy/data-inventory.md` — 新增存放個資的欄位、日誌欄位或外部處理者時，在同一個改動裡更新

## Conventions

- Backend is Python 3.14 managed with `uv` (`backend/pyproject.toml`, `backend/uv.lock`); frontend uses `pnpm`. `scripts/` run on the same Python 3.14 through `uv run --no-project --python 3.14 python scripts/<name>.py` (lefthook, Makefiles and CI do), never the host `python3`.
- The HTTP contract is generated: `make -C backend gen` writes `backend/api/openapi.json` from the routes and their `schemas.py` models (`gen-check` fails when it is stale), and the frontend generates its types from it (`pnpm gen:api`). Requests are checked by the routes' models and `app.http.route.ApiRoute`; tests check every response against the generated contract (`backend/tests/apitest.py` `checked_client`).
