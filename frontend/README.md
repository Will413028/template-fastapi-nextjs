# Frontend — Next.js 16

Part of the [template-fastapi-nextjs](../README.md). Uses **pnpm** (not npm/yarn/bun).

```bash
pnpm install        # install dependencies
pnpm dev            # dev server (http://localhost:3000, Turbopack)
pnpm build          # production build (standalone output)
pnpm lint           # tsc --noEmit + Biome
pnpm format         # Biome format --write
pnpm test           # Vitest
pnpm gen:api        # regenerate src/lib/api/schema.d.ts from backend/api/openapi.json
```

The browser calls the API on its own origin under `/api`. `pnpm dev` on the host
forwards `/api` to `API_URL` (default `http://localhost:8000`, see `.env.example`);
on Vercel, the `/api` rewrite in `vercel.json` does (see `docs/deploy-vps.md`).

See [`AGENTS.md`](./AGENTS.md) for architecture, the auth flow, and conventions.
Normally you run the whole stack from the repo root with `make dev`.
