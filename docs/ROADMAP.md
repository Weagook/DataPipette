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
- [ ] Define a single-table visual dataset draft
- [ ] Validate, preview, and publish through Celery
- [ ] Stream rows into local Parquet storage
- [ ] Query snapshot rows with DuckDB and cursor pagination
- [ ] Expose freshness and refresh errors through API and UI

## Phase 2 — Governed dataset engine

Outcome: teams can safely build and share production-shaped datasets.

- Groups, roles, ACL grants, and connection visibility scopes
- Foreign-key suggestions, table aliases, `INNER JOIN`, and `LEFT JOIN`
- Advanced read-only SQL datasets
- S3/MinIO result storage
- Schedules, coalesced refresh, cancellation, retries, and cleanup
- Draft/published versions, schema comparison, and impact analysis
- Query Safety Advisor based on non-executing PostgreSQL `EXPLAIN (FORMAT JSON)`
- Explainable warnings for large scans, missing or unsuitable indexes, expensive joins/sorts, stale statistics, and oversized results
- Administrator policies that warn or block by estimated rows, planner cost, joins, timeout, result size, and concurrency, with audited privileged overrides
- Read-only index/query recommendations and estimate-versus-actual execution metrics; DatePipette never applies source DDL
- Audit events and administrator audit UI
- Performance benchmark at the approved baseline

## Phase 3 — Exploration and export

Outcome: analysts can inspect and extract governed detailed data.

- Saved dataset display settings
- Typed filters, sorting, and pagination
- CSV and XLSX background exports
- Export history, expiry, and signed-download flow
- Formula-injection and authorization tests

## Phase 4 — Charts and dashboards

Outcome: analysts create and share the first useful dashboard.

- Chart query specification and validation
- Bar, line, pie/donut, and KPI charts
- Aggregates and timezone-aware time buckets
- Drag-and-resize dashboard layout
- Multi-dataset dashboards with independent failure states
- Dashboard viewer access without detailed dataset access

## Phase 5 — Headless integrations and production readiness

Outcome: another application can securely embed DatePipette backend capabilities.

- Service accounts and scoped expiring/non-expiring API tokens
- Generated TypeScript client and integration examples
- Rate limiting, idempotency, optimistic concurrency, and deprecation policy
- Backup and restore procedures
- Operational dashboards, alerts, and capacity guidance
- Threat-model review and release-candidate security hardening
- Production Docker Compose documentation

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
