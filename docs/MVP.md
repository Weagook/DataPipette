# MVP scope

## Outcome

The MVP is complete when a new single-company installation can connect a PostgreSQL database, publish a visual dataset snapshot, explore and export its rows, create a chart, place that chart on a dashboard, and share the dashboard with a viewer through both the official UI and the public API.

## Primary end-to-end scenario

1. An administrator creates a local account and a read-only PostgreSQL connection.
2. The administrator grants an analyst access to selected source schemas or tables.
3. The analyst creates a private visual dataset from one or more tables.
4. DataPipette suggests joins from foreign keys; the analyst confirms `INNER JOIN` or `LEFT JOIN` relationships.
5. The analyst validates and previews a draft.
6. Publishing starts a background refresh and streams the result into Parquet.
7. A completed snapshot becomes active atomically.
8. The analyst browses paginated detailed rows and applies temporary typed filters.
9. The analyst exports the filtered result to CSV or XLSX.
10. The analyst creates a chart with persistent filters, grouping, and aggregation.
11. The analyst places the chart on a dashboard and grants a viewer access.
12. The viewer sees aggregated chart data but cannot open or export the underlying dataset.

## Included

### Identity and authorization

- Local user accounts
- `Admin`, `Analyst`, and `Viewer` roles
- Groups and additive object ACL grants
- Private-by-default resources
- Service accounts and bearer API tokens
- Expiring and non-expiring tokens
- Token revocation, rotation, last-used metadata, and narrowing scopes

### PostgreSQL connections

- Multiple connections per installation
- Host, port, database, username, password, SSL mode, and optional CA certificate
- Encrypted stored secrets
- Connection grants scoped to all visible objects or an allowlist of schemas and tables
- Foreign-key metadata discovery

### Datasets

- Visual definitions with selected columns, aliases, `INNER JOIN`, and `LEFT JOIN`
- Foreign-key suggestions and explicit manual join conditions
- A single read-only advanced `SELECT` or `WITH` statement
- Draft, validation, preview, impact analysis, and atomic publication
- One active snapshot per dataset
- Manual and scheduled refresh
- Stale-if-error behavior
- Tabular display configuration and typed temporary filters

### Snapshots and query execution

- Streaming PostgreSQL reads
- Parquet output
- Local development storage and S3-compatible production storage
- DuckDB analytical reads over snapshots
- Server-side pagination
- Timezone-aware timestamp display and bucketing

### Charts and dashboards

- Bar, line, pie/donut, and KPI charts
- `COUNT`, `COUNT DISTINCT`, `SUM`, `AVG`, `MIN`, and `MAX`
- Time buckets: hour, day, week, month, quarter, and year
- Dashboards with charts from multiple datasets
- Independent chart loading, freshness, and failure state
- Viewer access to aggregated dashboard results without detailed dataset access

### Exports

- Asynchronous CSV and XLSX exports
- Current temporary filters applied
- Private files retained for 24 hours
- Five-minute signed download URLs that may be reissued while the file exists

### Operations

- Docker Compose
- Health and readiness endpoints
- Structured logs, request IDs, metrics, and audit events
- Append-only application audit log with configurable retention
- Reproducible benchmark data and performance tests

## Explicitly deferred

- OIDC or SAML SSO
- Additional source databases
- Cross-source queries
- Dataset dependency graphs
- `RIGHT`, `FULL`, and `CROSS JOIN` in the visual builder
- Calculated fields in the visual builder unless added by a later approved decision
- Snapshot history and rollback
- Email, webhook, or chat notifications
- SSH tunnels, PostgreSQL client certificates, and cloud IAM database authentication
- Kubernetes manifests and Helm charts
- Median, percentile, rolling-window, and custom aggregate functions

## Acceptance baseline

- A snapshot of 5 million rows and up to 50 columns completes without loading the full result into memory.
- Reference environment: 4 CPU and 8 GB RAM for a single node.
- Twenty concurrently active interactive users remain supported under the documented benchmark workload.
- One worker node supports up to two concurrent heavy refresh/export jobs.
- Additional API instances and workers can be added without changing application code.
- A failed refresh never replaces the last successful snapshot.
- API and UI clearly expose stale, refreshing, failed, and ready states.
- Every externally visible capability has authorization checks, tests, and OpenAPI documentation.
