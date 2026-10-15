# ADR-0039: Governed background analytical work

- Status: Accepted
- Date: 2026-10-15

## Context

CSV/XLSX workers already bounded DuckDB memory, threads, temporary disk, fetch size, and rows through deployment settings, but an administrator replacement did not affect them. Export generation had no time or artifact-size ceiling and no policy provenance. Celery process concurrency limited one worker only; scaling workers across hosts could allow refresh and export jobs together to exceed the intended heavy-work capacity.

Redis is an at-least-once task transport and must not become authoritative capacity state. A Redis semaphore would need expiring leases, renewal, ownership tokens, and recovery rules. Metadata PostgreSQL is already shared by every production worker and releases session advisory locks when a connection or process dies.

## Decision

- Extend the singleton Query Safety policy with export candidate timeout, maximum rows and artifact bytes, DuckDB memory and threads, temporary disk, and one `background_job_concurrency` value shared by refresh and export attempts. Existing `EXPORT_*` and `BACKGROUND_JOB_CONCURRENCY` deployment settings seed revision zero; export fetch batch size remains deployment tuning.
- Resolve one policy revision before background admission. Persist that revision when a refresh or export attempt is claimed and include it in safe lifecycle audit metadata and API resources.
- Use metadata-PostgreSQL session advisory locks for installation-wide admission. A short guard lock serializes admission decisions; the worker counts all active DatePipette heavy-job slots, acquires a free slot only below the captured limit, and releases the guard before execution.
- Acquire capacity before the durable job claim. When capacity is unavailable, leave the job queued and ask Celery to redeliver it after five seconds. Capacity deferral does not increment an attempt counter.
- Hold the session lock for the complete claimed attempt. Normal completion explicitly unlocks it; connection loss, worker death, or host failure releases it in PostgreSQL.
- Apply the captured export memory, thread, and temporary-disk settings to its isolated DuckDB connection. Bound source rows while fetching, bound the completed candidate before publication, and interrupt/check candidate generation against the captured timeout. Storage adapters retain their independent network timeouts.
- Return stable `export_timeout`, `export_limit_exceeded`, or `export_generation_failed` codes without exposing rows, filters, SQL, paths, or infrastructure errors.
- Keep interactive API admission process-local. Its short latency expectations require a separate distributed design and benchmark.

## Consequences

- Administrator changes affect the next admitted attempt without restarting workers; running work retains its captured revision.
- Refresh and export jobs on different processes or hosts cannot collectively exceed the current admission decision. Lowering the limit blocks new work based on the real active count and does not terminate running work.
- Metadata PostgreSQL holds one additional connection per running heavy job. The default budget is two, making this bounded and visible in capacity planning.
- Admission requires a direct or session-pooled metadata connection for the lifetime of a job; transaction-mode connection proxies are incompatible with session advisory ownership.
- Broker outages cannot erase durable intent, and Redis does not own capacity. Metadata database unavailability fails admission closed and prevents a job claim.
- An export may temporarily write a candidate before discovering the artifact-byte limit; failure removes the candidate and publishes no object.
- Export crash-recovery leases/cancellation and distributed interactive admission remain separate future work.

## Alternatives considered

- **Redis leases:** rejected because expiry during a valid long job could over-admit, while long expiries delay recovery; correct renewal and fencing require more state than the broker should own.
- **Celery worker concurrency only:** rejected because the value is local to a process or host and cannot coordinate refresh and export queues installation-wide.
- **A permanent slot table:** rejected because crash recovery would require leases and reaping. PostgreSQL already provides session ownership and automatic release.
- **Claim before admission:** rejected because capacity pressure would leave durable jobs falsely marked running and complicate safe redelivery.
