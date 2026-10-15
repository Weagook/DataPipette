# ADR-0042: Official React frontend foundation and dashboard reading slice

- Status: Accepted
- Date: 2026-10-15

## Context

DatePipette's backend and generated TypeScript client already expose the MVP capabilities, but the product needs a reference application that demonstrates those capabilities without becoming a second source of business rules. Building every editor simultaneously would create broad scaffolding with weak workflows. The first UI slice must establish navigation, session security, remote-state conventions, visual language, accessibility, and a useful end-to-end role boundary.

Dashboard reading is the smallest valuable cross-role workflow. It exercises browser sessions, Viewer authorization, shared resources, independent analytical queries, chart presentation, freshness, stale fallback, capacity failures, and responsive layout. The existing constrained dashboard-item response did not expose saved chart name/type, so a Viewer client could obtain aggregate rows but could not faithfully render the intended visualization without forbidden direct chart access.

## Decision

- Add an independently buildable Vite/React/TypeScript application under `frontend/` and consume `@datepipette/api-client` as the only request and DTO contract.
- Use TanStack Query for server state, React Router for route state, Apache ECharts for saved visualizations, and small React-local state for transient interaction. Do not add a second global state store.
- Deliver the UI as complete vertical slices. The first slice implements local session login/restoration/logout, a responsive role-aware shell, dashboard collection/detail, and independent bar, line, pie/donut, and KPI states.
- Keep browser credentials in secure cookies. Read the CSRF cookie only at mutation time; never persist passwords, session secrets, or bearer tokens in browser storage.
- Add saved `chart_name` and `chart_type` to chart query metadata, including dashboard-item queries. This is additive presentation metadata and does not grant direct chart-resource access.
- Lazy-load route modules and the visualization engine so login and overview do not pay the chart-rendering cost.
- Keep unfinished authoring and administration areas visibly identified as roadmap areas rather than attaching simulated actions to placeholder controls.

## Consequences

- The first official UI is immediately useful to Viewers while establishing reusable product foundations for analyst and administrator slices.
- External clients can faithfully render constrained dashboard items using the same additive metadata as the official frontend.
- The frontend remains independently testable and deployable, but its CI must build the local generated client before installing the file dependency.
- ECharts is a material lazy-loaded dependency; production builds and later performance budgets must continue to track its route-level cost.
- Dataset exploration, editors, sharing management, and administration remain explicit subsequent slices in `docs/FRONTEND.md`.

## Alternatives considered

- **Build every screen as static mockups first:** rejected because it would imply unsupported functionality and postpone API/authorization integration risk.
- **Let dashboard Viewers fetch chart resources:** rejected because it broadens authorization and exposes definition details that constrained execution does not require.
- **Infer chart type from result columns:** rejected because bar versus line and pie versus donut are saved presentation decisions, not properties of the rows.
- **Handwrite frontend DTOs:** rejected because the official UI must continuously validate the public OpenAPI contract used by external clients.
- **Add a global state library immediately:** rejected because session, route, query cache, and component-local state cover the accepted first slice.
