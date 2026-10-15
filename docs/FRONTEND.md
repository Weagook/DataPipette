# Official frontend

The DatePipette web application is the reference client of the public API. It must make the complete governed analytics workflow understandable without adding business behavior that external clients cannot reproduce.

## Experience principles

- **Data first:** maximize useful canvas space; navigation and chrome stay quiet.
- **State is visible:** loading, refreshing, stale fallback, failure, truncation, and last successful time are never hidden behind generic spinners.
- **Progressive complexity:** common visual workflows lead; SQL and governance detail remain available without overwhelming a Viewer.
- **Private by default:** sharing is a deliberate resource action, never implied by placement on a dashboard.
- **Safe confidence:** warnings explain evidence and consequence. The UI never claims that an index recommendation or planner estimate is certainty.
- **Keyboard and responsive by default:** all primary actions have visible focus states and every reading workflow works at a narrow viewport.

## Information architecture

| Area | Primary job | Roles |
| --- | --- | --- |
| Overview | Understand freshness, recent work, and attention needed | Admin, Analyst, Viewer |
| Dashboards | Read independently loading saved visualizations | Admin, Analyst, Viewer when shared |
| Datasets | Build, validate, publish, browse, filter, refresh, schedule, and export | Admin, Analyst |
| Charts | Create saved aggregate visualizations from published datasets | Admin, Analyst |
| Connections | Configure PostgreSQL and inspect or grant catalog visibility | Admin; scoped Analyst discovery |
| Access | Manage users, groups, memberships, service accounts, tokens, and grants | Admin; resource owners for object grants |
| Governance | Tune Query Safety and inspect audit events | Admin |

Viewer navigation deliberately omits detailed datasets, charts, source connections, exports, and administration. Concealed backend resources remain concealed in the UI rather than being shown as disabled actions.

## Route model

```text
/login
/
├── /overview
├── /dashboards
│   ├── /:dashboardId
│   └── /:dashboardId/edit
├── /datasets
│   ├── /new
│   └── /:datasetId
│       ├── /table
│       ├── /draft
│       ├── /refresh
│       ├── /exports
│       ├── /versions
│       └── /access
├── /charts
│   └── /:chartId
├── /connections
│   └── /:connectionId
└── /admin
    ├── /people
    ├── /service-accounts
    ├── /query-safety
    └── /audit
```

## Delivery slices

1. **Foundation and dashboard reading:** browser sessions, responsive shell, role-aware navigation, dashboard collection, and independent ECharts rendering with complete operational states.
2. **Dataset exploration:** dataset collection, freshness, schema-derived columns, Excel-like typed filters, stable pagination, and CSV/XLSX export lifecycle.
3. **Dataset studio:** connection catalog browser, visual table/join builder, advanced SQL editor, saved JSON draft editing, validation findings, preview, impact analysis, publication, refresh, and schedule controls.
4. **Chart studio:** visual type, dimension, measures, aggregation, persistent filters, sorting, time buckets, live bounded preview, and optimistic save.
5. **Dashboard studio and sharing:** chart palette, 12-column drag/resize canvas, multi-dataset layout, grants, and Viewer preview.
6. **Administration and operations:** connections, visibility scopes, users, groups, service accounts/tokens, Query Safety policy, and audit-event explorer.
7. **Hardening:** localization, end-to-end accessibility, performance budgets, browser matrix, operational telemetry, and production deployment documentation.

Each slice must end in a usable workflow backed only by documented API operations. Placeholder controls must not perform simulated production behavior.

## Visual direction

The interface uses a warm neutral canvas, dark ink navigation, precise blue/teal data accents, restrained borders, and compact typography. Cards are used only to group meaningful state. Charts share one accessible categorical palette and reserve amber/red for freshness or failure, never decoration. The DatePipette pipette/flow mark is reused from the repository artwork rather than introducing a second identity.

## Server-state behavior

- TanStack Query owns remote cache and request lifecycle; React owns only ephemeral interaction state.
- Session restoration gates protected routes. A `401` clears cached protected data and returns to login without exposing raw server text.
- Each dashboard item has its own query, retry control, and failure boundary. One chart never blocks the rest of the dashboard.
- Capacity exhaustion respects `Retry-After`; automatic retry remains bounded and visible.
- Stale snapshot data remains renderable with a persistent warning and safe refresh-error metadata.
- Mutations requiring CSRF read the non-HttpOnly CSRF cookie immediately before the request. Tokens and passwords are never persisted by the frontend.
