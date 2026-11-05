# template-fastapi-nextjs

Fullstack monorepo template for new web projects: the Python sibling of `template-go-nextjs`, with the same HTTP contract and the same Next.js frontend.

**Backend:** FastAPI (modular monolith organized by feature, hand-written OpenAPI contract, uv)
**Frontend:** Next.js 16 (App Router, TypeScript, Tailwind v4, shadcn/ui, i18n, generated API client), deployed to Vercel
**Infra:** Vercel rewrites `/api/*` to the backend on one origin; the backend and PostgreSQL run with Docker Compose on a VM behind a Cloudflare Tunnel

> Status: walking skeleton. The backend serves `GET /health` only; the database layer and the API operations land next.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (Compose v2.24.7+ for `make dev`, which runs `docker compose up --watch`)
- [uv](https://docs.astral.sh/uv/) — installs Python 3.14 and the backend's locked dependencies
- [pnpm](https://pnpm.io/) — `npm install -g pnpm`
- Python 3 — for the repo scripts (`make init-env`)

## Quick Start

Start a project with `make new NAME=my-project DEST=../my-project` (`REPO=owner/repo`, default
`Will413028/<NAME>`): it writes a new git repository with one commit on `main` and no remote, with the package,
database, role, image and brand names and the repository links set, and without `template/`, `docs/plans/` and the
lines that only concern the template. Work on the template itself in this repository:

```bash
make init-env        # .env from .env-example, database passwords in .secrets/
make dev             # backend rebuilt on change, Next.js dev server: http://localhost:3000
make -C backend sync check
```

### On GitHub, once per new repository

Repository settings do not come with a copy of the template. Under Settings → Code security, turn on Dependabot alerts,
Dependabot security updates (a fix pull request for each new advisory, without the 7-day cooldown that version updates
wait), secret scanning and push protection. The checks in `.github/workflows/` run without further setup.

## Project Structure

```
├── backend/                FastAPI app (contract in backend/api/openapi.yaml)
├── frontend/               Next.js 16 (React 19, TypeScript, Tailwind v4)
├── deploy/                 PostgreSQL initdb scripts
├── docker-compose.yml      VM stack (cloudflared with --profile tunnel)
├── docker-compose.dev.yml  Dev overrides
├── Makefile                Docker Compose shortcuts
├── scripts/                init_env, quality locks, commit policy
├── .env-example            Environment variable template
└── docs/                   Design rules, decisions (adr/), plans
```

`AGENTS.md` describes the checks, the backend layers and the conventions; `frontend/AGENTS.md` the frontend.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.14, FastAPI, uvicorn, Pydantic |
| API | Hand-written OpenAPI 3.0.3 contract, RFC 9457 problems |
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS v4, shadcn/ui, TanStack Query, React Hook Form + Zod |
| API client | openapi-typescript + openapi-fetch |
| i18n | next-intl (English, Traditional Chinese) |
| Checks | ruff, pyright (strict), import-linter, vulture (backend); Biome, knip, dependency-cruiser (frontend) |
| Testing | pytest (backend), Vitest + RTL (frontend) |
| Infra | Vercel (frontend), Docker Compose on a VM, Cloudflare Tunnel |
