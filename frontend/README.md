# SpendSift frontend

React 19 + TypeScript app built with Vite. In normal use it is served as static files by the FastAPI backend on `127.0.0.1:8765`; see the [root README](../README.md) for running the whole app.

## Scripts

Run from `frontend/` after `npm install`:

| Command | What it does |
|---|---|
| `npm run dev` | Vite dev server with HMR; `/api/*` is proxied to `http://127.0.0.1:8765`, so start the backend too. Runs `gen:api` first. |
| `npm test` | Vitest (jsdom) test suite, run once. |
| `npm run build` | Type-check (`tsc -b`) and production build into `dist/`. Runs `gen:api` first. |
| `npm run gen:api` | Exports the backend OpenAPI schema (`../.venv/bin/python -m backend.app.server.openapi openapi.json`) and generates `src/shared/api/schema.d.ts` from it. Needs the repo's Python venv. |
| `npm run lint` | ESLint. |

`openapi.json` and `src/shared/api/schema.d.ts` are generated and gitignored; run `npm run gen:api` (or `dev` / `build`) on a fresh checkout before type-checking.

Structure, routing and the data layer are described in [docs/product/frontend/architecture.md](../docs/product/frontend/architecture.md).
