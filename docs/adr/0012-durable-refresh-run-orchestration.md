# ADR-0012: Durable idempotent refresh-run orchestration

- Status: Accepted
- Date: 2026-10-09

## Context

Dataset refresh crosses an API process, Redis, a Celery worker, source PostgreSQL, and eventually snapshot storage. Broker delivery is at least once: an API may save a request but fail while publishing it, and a worker can be terminated after doing work but before acknowledging its message. Redis therefore cannot be the authoritative record of whether a refresh was requested, started, or finished.

The snapshot writer is not implemented yet. Introducing the queue before storage must not create a false successful publication state.

## Decision

- Store refresh runs and request-to-run idempotency mappings in metadata PostgreSQL. Runs use `queued`, `running`, `succeeded`, and `failed`; cancellation is deferred.
- Require the current draft ETag and an `Idempotency-Key` when requesting a refresh. Store only a SHA-256 digest of the key, scoped to actor and dataset.
- Require a current `valid` or `valid_with_warnings` validation artifact for the exact requested revision.
- Serialize creation with a dataset-row lock. Allow at most one running and one queued run per dataset. Additional requests coalesce into the queued run and retain their own idempotency mappings.
- Persist a run before publishing its task. Record dispatch attempts and keep `dispatched_at` empty when publication fails, so the same request can safely dispatch it again.
- Send only the run UUID through Redis, use it as the deterministic Celery task identifier, and reload all authoritative state in the worker.
- Configure JSON-only messages, late acknowledgement, rejection on worker loss, and a prefetch multiplier of one. The database claim operation makes duplicate delivery a no-op and defers a queued successor while another run is active.
- Audit lifecycle transitions without idempotency-key plaintext, SQL, source rows, or raw infrastructure errors.
- Until the Parquet writer exists, the production executor moves a claimed run to `failed` with `snapshot_writer_unavailable`. Test executors may prove the successful terminal transition, but the product never reports a snapshot success it did not produce.

## Consequences

- Clients can retry ambiguous API failures without creating duplicate work.
- Losing Redis messages does not erase durable intent, although automatic reconciliation of undispatched runs remains future operational work.
- Late acknowledgement and deterministic task IDs reduce duplicate effects but do not provide exactly-once delivery; correctness comes from transactional database transitions and idempotent executors.
- A worker lost after claiming a run can leave it in `running`; lease-based stale-run recovery is required before long source execution is enabled.
- The queue foundation is independently testable before source streaming and Parquet publication are added.
- A queued successor is bounded, so repeated refresh requests cannot create an unbounded per-dataset backlog.

## Alternatives considered

- Celery result state as the source of truth was rejected because broker/backend retention and delivery are operational concerns, not durable product metadata.
- Publishing before database commit was rejected because a fast worker could observe a missing run and the API could leave an orphan task after rollback.
- Passing definitions, SQL, or credentials in task arguments was rejected because messages can be duplicated, logged, or inspected and may become stale.
- Reporting success without a snapshot writer was rejected because it would violate atomic publication and mislead API clients.
- An unbounded queue per dataset was rejected because it wastes source capacity and mostly executes obsolete refresh intent.
