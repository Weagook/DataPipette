# ADR-0024: Refresh cancellation and bounded execution retries

- Status: Accepted
- Date: 2026-10-11

## Context

Refresh runs are durable PostgreSQL resources executed through at-least-once Celery delivery. A source outage or temporary object-storage failure should not require a new user request, while validation, authorization, schema, and data-shape failures should not be repeated blindly. Owners also need to stop obsolete queued or long-running work without treating Celery process state as authoritative. Cancellation and retry must preserve the last successful snapshot and the existing invariant of at most one running and one queued run per dataset.

## Decision

- Extend refresh state with terminal `cancelled`. Persist `cancel_requested_at`, `cancelled_at`, and a nullable retry due time `next_attempt_at`; retain aggregate `attempt_count` and lifecycle audit events.
- Expose an owner-authorized, CSRF-protected `POST /api/v1/datasets/{dataset_id}/refresh-runs/{run_id}/cancel` command. Queued runs cancel immediately. Running runs record durable cancellation intent and finish cooperatively. Repeating cancellation while requested or already cancelled is idempotent; succeeded and failed runs return a state conflict.
- Serialize cancellation and completion on the refresh-run row. Completion checks cancellation before snapshot publication, so a committed cancellation cannot activate a result. If publication commits first, success is terminal and a later cancellation conflicts.
- Poll cancellation intent from metadata PostgreSQL while executing and signal the executor in process. The Parquet writer checks at context, query, batch, validation, and pre-publication boundaries. A currently blocked source operation remains bounded by the configured PostgreSQL statement timeout.
- Mark only explicit source-availability and object-storage-availability failures retryable. Changed definitions, invalid validation, revoked access, unsupported types, malformed rows, local serialization failures, and unknown exceptions terminate immediately.
- Requeue retryable failures in PostgreSQL with bounded exponential backoff. Defaults are three total attempts, a five-second base, and a 300-second delay cap. Requeue clears `dispatched_at`; the independent scheduler republishes only when `next_attempt_at` is due.
- Give each running attempt a durable renewable lease. The worker renews it while polling cancellation. Duplicate delivery defers while the lease is fresh; late delivery or the scheduler may reclaim an expired lease within the same total-attempt limit. Exhaustion terminates with the safe `worker_lost` code.
- If another queued successor already exists when an older running attempt fails, do not retry the older run. Preserve the successor and finish the older run with its safe error code.
- Keep Redis/Celery non-authoritative. Duplicate or premature delivery is harmless because claims lock the durable run, reject terminal states, and defer future-due retries.

## Consequences

- Clients can display cancellation requested separately from cancellation completed and can poll the same run representation.
- Temporary outages recover without creating another idempotency key or losing operational history.
- A worker process lost after claiming work no longer leaves the run permanently stuck in `running`.
- Cancellation latency is cooperative rather than process-killing; source statement timeout remains the upper bound for a blocked database operation.
- Aggregate attempt count and audit events explain retries without introducing a child attempt table in this slice.
- A successfully uploaded object whose completion loses a cancellation race remains unreferenced and is later removed by ADR-0023 orphan reconciliation.

## Alternatives considered

- **Celery revoke as the source of truth:** rejected because revoke is broker/worker coordination, is not durable product state, and cannot safely resolve publication races.
- **Retry every failure:** rejected because permanent schema, access, and data-shape errors waste source capacity and obscure remediation.
- **Sleep inside a worker between attempts:** rejected because it occupies a scarce worker slot and is not durable across restarts.
- **Create a new run for every automatic retry:** rejected for the MVP because one durable run with an aggregate attempt count keeps idempotency and UI polling simpler.
- **Cancel the queued successor to retry the older run:** rejected because the newer intent should win.
