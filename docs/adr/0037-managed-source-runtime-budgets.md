# ADR-0037: Managed source preview and refresh runtime budgets

- Status: Accepted
- Date: 2026-10-14

## Context

Planner estimates reduce risk before execution but do not bound actual source work: estimates can be wrong, statistics can be stale, and a permitted query can still return too much data. Preview and refresh already had process-level statement timeouts and fixed result ceilings, but administrators could not tune them through the revisioned policy and a worker restart was required for deployment-setting changes.

Preview and refresh have different latency and result-shape expectations. Preview is synchronous and returns JSON to a person; refresh is asynchronous and streams a complete result into an immutable Parquet candidate. DuckDB snapshot queries use a different execution engine and resource model, so their memory, temporary disk, thread, response, and concurrency controls are deliberately outside this decision.

## Decision

- Extend the singleton Query Safety policy with preview statement timeout, maximum requested rows, maximum encoded response bytes, refresh statement timeout, and maximum result rows.
- Treat validated deployment settings as revision-zero defaults. Persist complete administrator replacements under the existing CSRF, ETag, and redacted audit contract.
- Resolve policy only after preview resource authorization, immediately before source execution. Reject a requested limit above the current policy and return the applied policy revision with successful results.
- Keep hard preview ceilings of 200 rows and one MiB. Policy may narrow but never expand them.
- Resolve policy once at the beginning of every Celery refresh job. Existing worker processes therefore observe a replacement on the next job; an already-running job retains the captured values.
- [ADR-0039](0039-governed-background-analytical-work.md) subsequently places refresh attempts under the same installation-wide admission limit as exports and persists their captured policy revision.
- Check refresh row count before writing every streamed batch. If the next batch exceeds the budget, close and remove the local candidate, publish no object, activate no snapshot, and finish with stable code `result_row_limit_exceeded`.
- Do not make a planner override bypass runtime budgets. Do not retry a row-budget failure because the same definition and policy would deterministically fail again.

## Consequences

- Administrators can reduce source exposure without redeploying or restarting API and worker processes.
- Preview clients can associate returned business rows with the exact policy revision that governed execution.
- Failed oversized refreshes preserve stale-if-error behavior: a prior active snapshot remains readable, while first publication remains unavailable with a safe error.
- The refresh row guard is streaming and memory-bounded, but PostgreSQL may produce one additional fetch batch before DatePipette detects the excess. Statement timeout and the read-only source role remain independent defenses.
- Together with ADR-0038 and ADR-0039, the runtime portion of QSA-004 now covers source, interactive analytical, export, and shared heavy-worker execution boundaries.

## Alternatives considered

- **Keep deployment-only values:** rejected because administrator changes would require process restarts and lack revisioned governance.
- **Read policy only when the worker starts:** rejected because long-lived workers would enforce stale values.
- **Publish a truncated refresh snapshot:** rejected because a dataset snapshot must represent the complete query result, not an undocumented sample.
- **Count rows before streaming:** rejected because it adds a second potentially expensive source query and introduces a race between count and extraction.
- **Combine source and DuckDB controls now:** rejected because the engines have different enforcement points and an incomplete shared policy would overstate safety.
