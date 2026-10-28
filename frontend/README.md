# DataPipette frontend

The official React application is a reference client of the same public API available to external integrations. It implements local-account sign-in and session restoration, a responsive role-aware shell, dashboard reading with independent bar, line, pie/donut, and KPI items, dataset exploration and CSV/XLSX exports, the visual, Canvas Builder, and Advanced SQL dataset studio, the chart and dashboard studios with sharing, connection, access, and governance administration, and English/Russian localization.

## Local development

Build the generated client once, install the locked frontend dependencies, start the API, and then start Vite:

```bash
npm ci --prefix clients/typescript
npm run build --prefix clients/typescript
npm ci --prefix frontend
uv run --package datapipette datapipette
npm run dev --prefix frontend
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` to `http://127.0.0.1:8000`, preserving same-origin browser cookies. Set `VITE_API_BASE_URL` only when an environment intentionally serves the API from another origin and configures that browser security boundary.

## Verification

```bash
npm run typecheck --prefix frontend
npm test --prefix frontend
npm run build --prefix frontend
npm run budget --prefix frontend
```

`npm run check --prefix frontend` runs all four steps in order; `budget` enforces the gzip transfer limits described in [`docs/FRONTEND.md`](../docs/FRONTEND.md) against the latest build. The production build is emitted to the ignored `frontend/dist/` directory. Browser passwords and API tokens must never be persisted in local storage, session storage, IndexedDB, source, or build-time environment variables.

Product navigation, delivery slices, and accessibility and performance boundaries are documented in [`docs/FRONTEND.md`](../docs/FRONTEND.md).
