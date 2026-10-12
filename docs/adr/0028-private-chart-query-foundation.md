# ADR-0028: Private chart specifications and snapshot aggregate queries

- Status: Accepted
- Date: 2026-10-12

## Context

DatePipette can publish immutable Parquet snapshots and expose a governed detailed-row contract, but it has no persistent analytical query that a frontend can render as a chart. Dataset schema impact already reserves chart dependency references; inventing those references before charts existed was intentionally deferred. The first chart slice must remain API-first, preserve dataset authorization, avoid source PostgreSQL during interactive reads, and provide deterministic bounded results.

## Decision

- Store each chart as a private owner-bound metadata resource referencing exactly one dataset. Administrators may operate every chart; an active analyst may operate only owned charts and must retain effective `view_rows` access to the dataset for create, update, and query operations.
- Store a schema-versioned JSONB definition containing a chart type, zero or one dimension, one to five measures, persistent typed filters, result sorts, and a result limit. Field references use stable published dataset field UUIDs; measures also have stable UUIDs for result sorting and client mapping.
- Support `bar`, `line`, `pie`, `donut`, and `kpi` type discriminators. KPI has no dimension; other initial chart types have one dimension. Pie, donut, and KPI accept one measure in this version.
- Validate `COUNT`, `COUNT DISTINCT`, `SUM`, `AVG`, `MIN`, and `MAX` against the active published field types. Reuse the dataset row-query filter vocabulary and normalization policy.
- Execute saved definitions synchronously through parameterized DuckDB SQL over the active Parquet snapshot. Apply the existing timeout, memory, thread, temporary-disk, concurrency, and response-byte limits. Never query source PostgreSQL for chart reads.
- Return stable result column keys plus dimension/measure metadata, chart revision, snapshot identity, freshness, and stale-fallback error metadata. Add deterministic dimension tie-breakers to explicit result sorting.
- Use chart ETags for optimistic full replacement. Persist every referenced field in a normalized dependency table so schema impact reports affected charts and blocks breaking changes.
- Do not add destructive deletion yet. Chart archival and deletion require a separate lifecycle decision together with dashboard references.

## Consequences

- The official frontend and integrations can build and execute the same chart contract without privileged draft access.
- Grant revocation affects the next chart query; owning a chart never expands dataset permissions.
- A saved definition can become invalid only after a separately authorized forced breaking publication; the query API reports that conflict without deleting the chart.
- Initial chart results are deliberately small aggregate tables rather than renderer-specific payloads. Presentation and dashboard layout remain client concerns.
- Time buckets, chart sharing, dashboard-context viewer access, and invalid-state persistence remain later slices.

## Alternatives considered

- **Store generated SQL:** rejected because it would weaken stable-field validation, portability, and dependency analysis.
- **Execute charts against source PostgreSQL:** rejected because interactive reads must preserve snapshot consistency and avoid source load.
- **Allow arbitrary dimensions and expressions immediately:** rejected to keep validation, result shape, and performance bounded in the first vertical slice.
- **Cascade-delete charts:** rejected because dashboard dependency and recovery semantics have not been accepted.
