# Roadmap

The roadmap is outcome-based and intentionally has no date promises. Each phase should leave the repository demonstrably working.

## Phase 0 — Foundation

Outcome: contributors can understand, run, verify, and safely change a small backend service.

- Approve product, architecture, API, security, and ADR foundations
- Reorganize the repository into backend/frontend/deploy/docs areas
- Configure Python 3.12, uv, linting, typing, tests, and CI
- Add FastAPI health/readiness endpoints and structured configuration
- Add metadata PostgreSQL, Alembic, Redis, and Docker Compose
- Establish structured logging, request IDs, and test fixtures

## Phase 1 — First dataset vertical slice

Outcome: an administrator connects PostgreSQL and an analyst publishes and browses one private single-table snapshot.

- [x] Bootstrap administrator, local authentication, and administrator-managed local accounts
- [x] Create encrypted PostgreSQL connections and connection tests
- [x] Inspect schemas, tables, columns, and keys
- [x] Define a single-table visual dataset draft
- [x] Validate one-table drafts and compile a non-executing PostgreSQL plan
- [x] Preview a bounded set of source rows for the validated draft
- [x] Establish durable idempotent refresh runs and Celery worker orchestration
- [x] Publish a validated one-table definition through Celery
- [x] Stream rows into validated local Parquet storage
- [x] Query snapshot rows with DuckDB, typed filters, deterministic cursor pagination, and bounded responses
- [x] Expose freshness and refresh errors through the headless API (official UI deferred until frontend scaffolding)

## Phase 2 — Governed dataset engine

Outcome: teams can safely build and share production-shaped datasets.

- [x] Groups, role ceilings, and additive dataset ACL grants
- [x] Connection visibility scopes and owner-authorized analyst dataset authoring
- [x] Foreign-key suggestions, table aliases, `INNER JOIN`, and `LEFT JOIN`
- [x] Interval/daily refresh schedules, coalesced due runs, and undispatched-run recovery
- [x] Advanced read-only SQL datasets
- [x] S3/MinIO-compatible result storage
- [x] Safe superseded-snapshot, expired-export, and orphan-object cleanup
- [x] Refresh cancellation and execution retry policies
- [x] Draft/published versions, schema comparison, and impact analysis (schema impact implemented; chart dependency blocking activates with chart resources)
- [x] Extend the initial non-executing PostgreSQL plan guard into a configurable Query Safety Advisor foundation
- [x] Explainable warnings for large scans, absence of visible indexes, expensive joins/sorts, stale statistics, and oversized results
- [x] Revisioned administrator planner policies for estimated rows, planner cost, and joins, with audited privileged overrides
- [x] Source preview/refresh runtime policies for statement timeout and result-size bounds
- [x] Interactive DuckDB runtime policies for timeout, response size, memory, temporary disk, threads, and shared concurrency
- [x] Background export runtime policy and cross-instance heavy refresh/export admission control
- [x] Cross-instance interactive-query admission control
- [x] Read-only index/query recommendations and estimate-versus-actual execution metrics; DatePipette never applies source DDL
- [x] Filtered, paginated administrator audit-event API
- Administrator audit UI (frontend phase)
- Performance benchmark at the approved baseline

## Phase 3 — Exploration and export

Outcome: analysts can inspect and extract governed detailed data.

- [x] Saved dataset display settings exposed from the active published version
- [x] Typed filters, sorting, and pagination
- [x] CSV and XLSX background exports
- [x] Export history, logical expiry, and signed-download flow
- [x] Browser-reachable direct download for local, private MinIO, and external S3 delivery topologies
- [x] Formula-injection and authorization tests

## Phase 4 — Charts and dashboards

Outcome: analysts create and share the first useful dashboard.

- [x] Chart query specification, aggregate/type validation, private persistence, and bounded snapshot execution
- [x] Bar, line, pie/donut, and KPI chart query contracts
- [x] Aggregates and timezone-aware time buckets
- [x] Private versioned drag-and-resize dashboard layout contract
- [x] Multi-dataset dashboards with independent per-item loading and failure contracts
- [x] Dashboard viewer access without detailed dataset access

## Phase 5 — Headless integrations and production readiness

Outcome: another application can securely embed DatePipette backend capabilities.

- [x] Service accounts and scoped expiring/non-expiring API tokens (`dashboards:read` initial scope)
- [x] Generated TypeScript client and service-token dashboard integration example
- [x] Self-contained one-command evaluation Compose topology
- Rate limiting, idempotency, optimistic concurrency, and deprecation policy
- Backup and restore procedures
- Operational dashboards, alerts, and capacity guidance
- Threat-model review and release-candidate security hardening
- Production Docker Compose documentation

## Phase 6 — Official frontend

Outcome: every MVP capability is understandable and usable through a polished reference React application backed only by the public API.

- [x] Session login, restoration, logout, responsive shell, and role-aware navigation
- [ ] English-default interface with complete Russian localization and a persisted profile language preference; API error codes and technical identifiers remain stable
- [ ] Desktop-only node-canvas dataset authoring alongside the guided visual builder and Advanced SQL
  - PostgreSQL Table, Join, Select Columns, Rename & Format, and Dataset Output nodes
  - FK-suggested and manual compatible-column joins
  - persisted graph and viewport, minimap, zoom, undo/redo, edge deletion, selected-node settings, and safe auto-layout
- [x] Dashboard collection and independent bar, line, pie/donut, and KPI item rendering
- [x] Dataset exploration with typed Excel-like filters, pagination, freshness, and exports
- [x] Visual/SQL dataset studio with validation, preview, impact, publication, refresh, and schedules
- [x] Chart studio with aggregations, filters, sorting, and timezone-aware buckets
- [x] Dashboard layout editor and user/group sharing
- [x] PostgreSQL connection administration, safe testing, live catalog inspection, and exact visibility scopes
- [x] Local identity, group membership, service-account, and one-time token administration
- [x] Query Safety policy and audit-event administration
- [x] Accessibility, responsive browser matrix, performance budgets, and production deployment

## Later

- OIDC/SSO
- Additional PostgreSQL authentication methods
- Additional data sources
- Dashboard-level filter mapping
- Snapshot history
- Kubernetes and Helm
- External immutable audit sinks
- Advanced metrics and time-series calculations

Items move into an implementation phase only after acceptance behavior and verification commands are written in an execution plan.
