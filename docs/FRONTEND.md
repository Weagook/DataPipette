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
├── /access
└── /governance
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

Slices 1 through 6 are implemented. The interface defaults to English and can be switched immediately to Russian from the signed-in profile menu. The choice is persisted on the user through the public preference API and restored with the browser session; API codes, audit action names, identifiers, SQL, and source-object names remain invariant. Dataset exploration deliberately keeps filter edits local until Apply, translates semicolon-separated values to the public typed `in` operator, and resets cursor history whenever the query shape or active snapshot changes. Exports capture the currently applied columns, filters, sorting, and snapshot; their durable job history can issue a fresh short-lived download link until the stored artifact expires.

The administrator-only Governance area exposes the complete managed Query Safety policy rather than a lossy subset. Updates replace the policy with CSRF protection and its current opaque ETag, retain unsaved values when the server rejects a stale revision, and surface the new revision after success. Its audit explorer keeps draft filters separate from applied filters, requests exact actor/action/outcome/resource/request/time constraints, resets cursor history on every filter change, and renders event details as inert formatted JSON.

Dataset Studio persists the same structured JSON document exposed by the public API. Creation is a guided three-step workspace: deliberately select a connection and authoring mode, build the definition, then create its private draft. No connection is silently selected on the analyst's behalf; catalog-loading failures stay in context and expose a retry action. Visual authoring starts from live scoped catalog metadata and immediately opens the complete builder, where source columns can be included or excluded, output names and presentation can be adjusted, table aliases are editable, and additional relations are admitted only through an explicit visible foreign-key suggestion. Advanced SQL authoring presents the query editor directly, keeps the read-only statement and its declared stable output fields together, and never inserts an executable example query. The structured JSON remains available as an optional inspection view rather than the primary workflow. Stable relation, join, and field identifiers survive subsequent edits. Optimistic draft saves invalidate earlier checks; validation, preview, impact analysis, and publication are enabled only for the exact current revision. Publication monitors the durable refresh run, keeps the last-known-good snapshot readable on failure, and exposes the installation-timezone refresh schedule after first publication.

Canvas Builder is the desktop spatial editor for that same visual document, not a separate dataset kind. It derives PostgreSQL Table, Join, Select Columns, Rename & Format, and Dataset Output nodes from the safe relation/join/field contract. Foreign-key suggestions and manually selected compatible-column joins create ordinary explicit join-tree entries. Dragged positions and viewport are bounded authoring metadata saved with schema-version-2 drafts; minimap, zoom, fit view, auto-layout, selected-node settings, edge deletion, and bounded in-session undo/redo never bypass ETag saves or backend validation. Deleting a join removes its right-hand relation subtree to preserve the rooted acyclic graph. Below 1050 px the canvas controls are absent and Guided Visual remains available without modifying saved layout.

Chart Studio derives field, type, and filter capabilities from the active published exploration contract. Its guided definition supports bar, line, pie, donut, and KPI forms; one dimension or KPI shape; stable aggregate measures; typed permanent filters; result sorting and limits; and installation-timezone date buckets. Creation and complete replacement use the public private-chart API and opaque revision ETags. The preview never executes an unsaved document: it queries only the saved definition, clearly marks dirty state, and preserves freshness, stale-fallback, truncation, and accessible-data metadata from the bounded snapshot response.

Dashboard Studio persists the public schema-versioned 12-column document without a second responsive layout format. It offers an owner-scoped chart palette, deterministic first-fit placement, drag movement, exact keyboard-accessible movement and sizing controls, collision/boundary prevention, compaction, multi-dataset visibility, and optimistic complete replacement through the dashboard ETag. A saved dashboard deliberately grants and revokes view-only access for people or groups. The Viewer preview is the ordinary saved dashboard route, so owners and recipients exercise the same independently loading constrained item-query contract; unsaved layouts never execute.

Connections administration exposes only the safe metadata returned by the public API. Passwords and CA certificates are write-only: an empty password edit preserves the encrypted value, while CA removal is a separate explicit action. Administrators can test saved settings and see only the timestamp, PostgreSQL version, and stable safe failure code; the live catalog progressively exposes visible schemas, relations, columns, primary keys, and foreign keys. Direct analyst and group grants are created from those live selections at exact `all`, `schema`, or `table` scope and can be revoked immediately. Non-administrators cannot enter these routes; analysts consume only their already-scoped catalog inside Dataset Studio.

Access administration keeps local people, reusable groups, and external integration identities in one admin-only workspace. People can be created, assigned a coarse role, disabled, or have their browser sessions revoked without conflating those operations. Groups expose explicit active-user membership. Each external system receives its own service account and independently revocable token history so rotation does not require downtime. A newly issued token is shown once and held only in component state; dismissing it removes recoverable plaintext while the API and subsequent list responses retain metadata and digest only.

## Hardening baseline

The shell exposes a keyboard skip link, route-specific document titles, a polite route announcer, visible focus, and an Escape-close mobile navigation that moves focus into the menu and returns it to the opener. Closed off-canvas navigation is removed from both pointer and accessibility interaction. Loading, recoverable failure, mutation feedback, snapshot warnings, and export feedback use live status or alert semantics. Charts stop animation when the operating system requests reduced motion and retain a structured accessible-data table.

The responsive acceptance matrix is deliberately small and reproducible. Chromium checks use the production Nginx image rather than Vite development behavior:

| Viewport | Representative device | Expected shell |
| --- | --- | --- |
| 360 × 800 | Narrow phone | Off-canvas navigation, single-column content, no document overflow |
| 768 × 1024 | Tablet/portrait | Off-canvas navigation, compact multi-column content where safe |
| 1280 × 800 | Laptop | Persistent navigation and full authoring workspace |
| 1600 × 1000 | Wide desktop | Centered content within the 1560 px canvas cap |

The production build enforces gzip transfer budgets with `npm run budget --prefix frontend`: 86 KiB for boot-entry JavaScript, 20 KiB for shared CSS, 12 KiB for the largest ordinary lazy route, 32 KiB for the shared TanStack Query runtime, 70 KiB for the deliberately isolated Canvas Builder runtime/editor, and 200 KiB for the deliberately isolated ECharts visualization runtime. The entry allowance increased from 80 KiB only after the desktop graph runtime required additional shared React DOM behavior; React Flow itself remains lazy and separately bounded. A budget increase requires an explicit review of the user-visible benefit; the Vite chunk-size warning is not disabled.

This baseline is not a WCAG conformance claim or a substitute for assistive-technology testing. It is a regression boundary covering keyboard access, semantic status, reduced motion, responsive overflow, transfer size, and the production gateway.

## Visual direction

The interface uses a warm neutral canvas, dark ink navigation, precise blue/teal data accents, restrained borders, and compact typography. Cards are used only to group meaningful state. Charts share one accessible categorical palette and reserve amber/red for freshness or failure, never decoration. The DatePipette pipette/flow mark is reused from the repository artwork rather than introducing a second identity.

## Server-state behavior

- TanStack Query owns remote cache and request lifecycle; React owns only ephemeral interaction state.
- Session restoration gates protected routes. A `401` clears cached protected data and returns to login without exposing raw server text.
- The authenticated user projection is the source of truth for interface locale. A profile change updates the UI optimistically, persists through a CSRF-protected mutation, and rolls back if the API rejects it; browser storage is not used.
- Each dashboard item has its own query, retry control, and failure boundary. One chart never blocks the rest of the dashboard.
- Capacity exhaustion respects `Retry-After`; automatic retry remains bounded and visible.
- Stale snapshot data remains renderable with a persistent warning and safe refresh-error metadata.
- Mutations requiring CSRF read the non-HttpOnly CSRF cookie immediately before the request. Tokens and passwords are never persisted by the frontend.
- Connection queries never contain secret material. Create/update forms discard plaintext password and CA input after a successful mutation, and connection tests render only redacted API results.
- Newly issued API-token plaintext exists only in ephemeral component state. It is never added to TanStack Query, browser storage, URLs, logs, or token-list responses, and a disabled service account cannot issue another token.
- Dataset exploration metadata includes only the safe display name, installation timezone, and effective export capability needed to render a reloadable direct link accurately; backend authorization remains authoritative for every row query and export action.
