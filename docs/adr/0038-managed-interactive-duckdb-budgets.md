# ADR-0038: Managed interactive DuckDB runtime budgets

- Status: Accepted
- Date: 2026-10-15

## Context

Dataset-table, saved-chart, and dashboard-item requests all execute synchronously over immutable Parquet snapshots through embedded DuckDB. They already used deployment-level timeout, response-byte, memory, thread, temporary-disk, and concurrency settings, but long-lived API processes could not observe administrator changes and row/chart executors had separate semaphores. The latter allowed aggregate concurrency to exceed the nominal setting.

Interactive work has a different latency and admission profile from background CSV/XLSX export jobs. Horizontal API replicas also need an explicit distinction between process-local resource protection and a future distributed admission protocol.

## Decision

- Extend the singleton Query Safety policy with interactive query timeout, encoded response bytes, memory, DuckDB threads, temporary disk, and concurrency. Existing validated `SNAPSHOT_QUERY_*` settings seed revision zero.
- Resolve the policy after caller authorization and active-snapshot lookup for every table, chart, and dashboard-item request. Capture one revision for the complete operation and return it in result metadata.
- Configure each isolated in-memory DuckDB connection from the captured policy. Use the same timeout for admission wait and query execution, and the captured byte budget for response serialization.
- Share one dynamic admission limiter between row and chart executors in an API process. A request enters only while the active count is below the concurrency value in its captured revision; chart requests reached through dashboards use the same path.
- Store the applied policy revision in redacted query audit metadata. Do not store query values, rows, SQL, cursor text, or filesystem paths.
- Treat validated policy maxima as hard ceilings. Planner overrides do not bypass runtime budgets.
- Keep API replicas independent. Distributed interactive admission requires separate latency semantics; [ADR-0039](0039-governed-background-analytical-work.md) separately governs export budgets and heavy refresh/export admission.

## Consequences

- Administrator replacements affect the next authorized request without restarting API processes.
- Row, chart, and dashboard workloads can no longer each consume the full configured concurrency independently inside one process.
- Lowering concurrency below the current active count blocks new work until enough captured operations finish; running DuckDB queries are not terminated by policy replacement.
- Per-process interactive admission scales predictably with API replicas, but installation-wide interactive concurrency equals the sum of replica capacity until distributed coordination is implemented.
- Response metadata and audit history identify the governing revision without exposing policy details to unauthorized resource requests.

## Alternatives considered

- **Rebuild executors after policy replacement:** rejected because application instances do not receive a reliable configuration-change signal and would diverge across replicas.
- **Keep separate row and chart semaphores:** rejected because the configured concurrency would not describe real aggregate DuckDB pressure.
- **Use Redis as a distributed semaphore immediately:** rejected because cancellation, lease expiry, process death, and degraded-broker behavior need a dedicated correctness design.
- **Apply this policy to exports:** rejected because background jobs need different timeouts, retry behavior, and worker-pool admission.
