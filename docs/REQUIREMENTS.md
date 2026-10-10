# Requirements

This document assigns stable identifiers to approved product requirements. Detailed implementation belongs in milestone plans and ADRs.

## Functional requirements

### Identity and access

- **IAM-001:** The system shall support local user accounts and the roles `Admin`, `Analyst`, and `Viewer`.
- **IAM-002:** The system shall support groups and grants to individual users or groups.
- **IAM-003:** New datasets, charts, dashboards, and exports shall be private by default.
- **IAM-004:** Effective grants shall be additive; explicit deny rules are outside MVP scope.
- **IAM-005:** External systems shall authenticate as service accounts using scoped bearer tokens.
- **IAM-006:** Tokens shall support optional expiration, immediate revocation, safe rotation, and last-used tracking.
- **IAM-007:** A token scope may narrow but never expand the service account's role and ACL permissions.

### Connections

- **CON-001:** Administrators shall manage multiple PostgreSQL connections.
- **CON-002:** Connection credentials shall never be returned after creation or written to logs.
- **CON-003:** Connection access may allow all database-visible objects or an allowlist of schemas and tables.
- **CON-004:** Visibility restrictions shall be enforced by the backend for metadata discovery, visual queries, preview, refresh, and manual SQL.
- **CON-005:** The system shall inspect PostgreSQL metadata and suggest relationships from foreign keys.

### Datasets

- **DAT-001:** A dataset shall use exactly one PostgreSQL connection and shall not depend on another dataset.
- **DAT-002:** A visual dataset shall store a structured definition rather than only generated SQL.
- **DAT-003:** The visual builder shall support explicit `INNER JOIN` and `LEFT JOIN` relationships.
- **DAT-004:** Visual datasets shall produce detailed rows without grouping or aggregation.
- **DAT-005:** An advanced dataset may contain one read-only `SELECT` or `WITH` statement, including aggregation and window functions.
- **DAT-006:** Dataset definitions shall have a published version and a separately editable draft.
- **DAT-007:** Publication shall require validation, a successful refresh, and atomic activation of the definition and snapshot.
- **DAT-008:** Breaking schema changes shall be blocked by default and shall identify affected charts.
- **DAT-009:** Explicitly forced breaking publication shall mark incompatible charts invalid instead of deleting them.
- **DAT-010:** A visual definition shall be a schema-versioned structured document whose source relations and output fields have stable identifiers.
- **DAT-011:** Draft replacement shall use optimistic revision control and shall never mutate the currently published definition.
- **DAT-012:** Explicit validation shall re-check live source objects, capture output types, and store a derived artifact bound to the exact draft revision.
- **DAT-013:** Changing a draft definition shall invalidate prior validation; a concurrent change shall prevent stale validation from being attached.
- **DAT-014:** Synchronous draft preview shall require validation of the same revision and enforce server-side row, response-size, statement-time, and memory bounds without creating a snapshot.
- **DAT-015:** An active analyst shall author and execute only owned datasets whose referenced source relations remain inside the analyst's current effective connection scope; administrators retain operational access.

### Query safety advisor

- **QSA-001:** Before a potentially expensive source query, the system shall obtain a PostgreSQL plan with `EXPLAIN (FORMAT JSON)` without `ANALYZE` and without executing the business query.
- **QSA-002:** The advisor shall combine planner estimates with visible catalog statistics such as approximate relation rows, relation and index sizes, available indexes, and statistics freshness.
- **QSA-003:** The advisor shall return explainable findings with stable codes and `info`, `warning`, or `blocked` severity. Findings may include large sequential scans, high estimated rows or cost, expensive sorts, Cartesian joins, type-mismatched joins, and stale statistics.
- **QSA-004:** Administrators shall configure separate safety limits for interactive preview and background refresh, including estimated rows, planner cost, join count, execution timeout, result size, and concurrency.
- **QSA-005:** Policy overrides shall require explicit permission and create a redacted audit event. A warning shall not be presented as certainty because PostgreSQL planner estimates can be inaccurate.
- **QSA-006:** DatePipette may recommend an index or query change but shall never execute DDL or create an index in the source database.
- **QSA-007:** Completed source executions shall capture bounded operational metrics so estimates can be compared with actual duration, result rows, transferred bytes, and snapshot size.

### Refresh and snapshots

- **REF-001:** Source PostgreSQL shall be queried for preview and refresh, not for ordinary table, chart, or dashboard reads.
- **REF-002:** Snapshot creation shall stream rows into Parquet without materializing the complete result in process memory.
- **REF-003:** Production snapshots shall use S3-compatible storage; local storage shall be supported for development and tests.
- **REF-004:** A dataset shall expose one active snapshot.
- **REF-005:** A new snapshot shall become active only after complete successful storage.
- **REF-006:** The previous snapshot shall remain active after refresh failure.
- **REF-007:** A successful publication shall schedule deletion of the replaced snapshot.
- **REF-008:** Manual and scheduled refresh requests shall be idempotent and coalesced to one running and at most one pending job per dataset.
- **REF-009:** Result APIs shall expose freshness, last success, refresh state, and the latest safe error summary separately from result rows.
- **REF-010:** Metadata PostgreSQL shall remain authoritative for refresh lifecycle state; broker messages shall contain only opaque identifiers and be safe to deliver more than once.
- **REF-011:** A persisted run whose broker dispatch fails shall remain retryable through the original idempotent request.
- **REF-012:** Snapshot writers shall derive a fixed output schema before reading rows and shall validate the completed object's schema, row count, byte size, and checksum before metadata activation.
- **REF-013:** Snapshot metadata shall store portable relative object keys; local filesystem storage shall not be treated as horizontally scalable production storage.

### Data exploration and exports

- **EXP-001:** Dataset rows shall be server-side paginated, sortable, and filterable with typed operators.
- **EXP-002:** Temporary table filters shall not mutate the dataset definition.
- **EXP-006:** Snapshot row cursors shall be opaque, tamper-evident, and invalidated when the snapshot or normalized query shape changes.
- **EXP-007:** Interactive snapshot reads shall enforce server-side limits for fields, filters, set values, sorting, page rows, response bytes, execution time, memory, CPU threads, and temporary disk.
- **EXP-003:** CSV and XLSX exports shall be background jobs and shall apply the current temporary filters.
- **EXP-004:** Export files shall be private, retained for 24 hours, and downloaded through five-minute signed URLs.
- **EXP-005:** A new signed URL may be issued while an export file exists; regeneration after expiry shall use the current snapshot.

### Charts and dashboards

- **VIS-001:** Charts shall reference datasets directly.
- **VIS-002:** Charts shall store persistent filters, dimensions, groupings, measures, sorting, and presentation configuration.
- **VIS-003:** MVP chart types shall be bar, line, pie/donut, and KPI.
- **VIS-004:** MVP aggregates shall be `COUNT`, `COUNT DISTINCT`, `SUM`, `AVG`, `MIN`, and `MAX`, validated against column types.
- **VIS-005:** Timestamp dimensions shall support hour, day, week, month, quarter, and year buckets in the installation timezone.
- **VIS-006:** A dashboard may contain charts from multiple datasets but shall not perform cross-dataset joins or calculations.
- **VIS-007:** A chart failure shall not prevent unrelated dashboard charts from rendering.
- **VIS-008:** Dashboard viewers may read aggregated chart results without receiving detailed dataset access.

### Auditing

- **AUD-001:** Security-sensitive and data-governance actions shall create append-only audit events.
- **AUD-002:** Audit events shall exclude passwords, token secrets, connection secrets, and complete business data.
- **AUD-003:** Audit events shall be administrator-readable through filtered, paginated APIs.
- **AUD-004:** Default retention shall be 365 days, with configurable or indefinite retention.

## Non-functional requirements

- **NFR-001 — Correctness:** Published data and metadata must describe the same immutable snapshot.
- **NFR-002 — Security:** Authorization must be enforced in the backend on every protected operation.
- **NFR-003 — Availability:** A failed refresh must not make a previously successful snapshot unavailable.
- **NFR-004 — Scalability:** API instances and workers must scale horizontally with shared PostgreSQL, Redis, and S3 state.
- **NFR-005 — Performance:** The MVP benchmark target is 5 million rows, up to 50 columns, 20 active users, and two heavy jobs on the reference node.
- **NFR-006 — Memory safety:** Refresh, query, and export flows must use bounded-memory or streaming designs.
- **NFR-007 — Observability:** Requests and background jobs must carry correlation identifiers and emit structured logs, metrics, and trace context.
- **NFR-008 — API stability:** Breaking public API changes require a versioning and deprecation path.
- **NFR-009 — Testability:** Core behavior must be testable without external cloud services through local PostgreSQL, Redis, and filesystem/MinIO adapters.
- **NFR-010 — Portability:** Docker Compose is the first deployment target; application design must not block later Kubernetes deployment.
