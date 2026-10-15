# ADR-0041: Installation-wide interactive query admission

- Status: Accepted
- Date: 2026-10-15

## Context

Table, chart, and dashboard-item requests share one interactive DuckDB concurrency value, but the original limiter applied only inside one API process. Adding replicas therefore multiplied the real installation load and made the administrator-managed value misleading. Interactive HTTP requests also have different latency semantics from durable background jobs: waiting in every replica can consume connections and request capacity while offering no fair global queue.

The admission authority must survive process loss, observe policy changes without restart, coordinate every query type, and avoid making Redis ephemeral capacity state authoritative.

## Decision

- Use metadata PostgreSQL session advisory locks in a namespace separate from background refresh/export admission. One held database session represents one admitted interactive query.
- Serialize admission decisions with a short guard lock, count all active interactive slots, and attempt a free slot only when the active count is below the concurrency value captured from the request's Query Safety policy revision.
- Apply the shared admission before DuckDB opens or materializes a snapshot. Row, chart, and dashboard-item requests all pass through the same adapter.
- Reject exhausted capacity immediately with `503 interactive_query_capacity_exceeded` and `Retry-After: 1`. Do not consume the DuckDB execution timeout while waiting in an HTTP queue.
- Fail closed through the existing query-unavailable boundary when metadata PostgreSQL cannot make the admission decision.
- Release the advisory slot after execution. PostgreSQL also releases it automatically when the owning session or process dies.
- Keep the in-process waiting limiter only as a test and adapter fallback when no distributed authority is supplied.

## Consequences

- The configured interactive concurrency value now describes aggregate table, chart, and dashboard-item pressure across API processes and hosts.
- Lowering the value rejects new requests until the real active count falls below the new limit; already admitted queries continue under their captured revision.
- Every concurrent interactive query holds one dedicated, non-pooled metadata PostgreSQL session so admission cannot exhaust the ordinary metadata-session pool. Deployments must budget direct database connections for the configured concurrency and must not place these sessions behind transaction pooling.
- Clients receive an explicit retry signal rather than an ambiguous timeout. Capacity rejection is recorded with a stable redacted audit error code.
- PostgreSQL remains a control-plane dependency for starting a query, while snapshot reads continue to use Parquet and DuckDB rather than source PostgreSQL.

## Alternatives considered

- **Keep independent per-process semaphores:** rejected because scaling replicas silently multiplies the configured limit.
- **Wait for a distributed slot:** rejected because unbounded HTTP waiters consume resources and have no accepted fairness or cancellation contract.
- **Use Redis counters or expiring leases:** rejected because crash recovery and lease renewal can admit too much work, while PostgreSQL session locks already provide ownership and automatic release.
- **Reuse background-job slots:** rejected because durable heavy jobs and latency-sensitive interactive queries require separate capacity budgets and failure behavior.
