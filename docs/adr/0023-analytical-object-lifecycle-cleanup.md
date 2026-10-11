# ADR-0023: Analytical object lifecycle cleanup

- Status: Accepted
- Date: 2026-10-11

## Context

Immutable snapshot publication and background export generation deliberately separate private object storage from transactional metadata PostgreSQL. This prevents partial publication, but it also creates three storage-lifecycle cases: superseded snapshots, logically expired exports, and unreferenced objects left when an upload succeeds but the following metadata transaction fails. Immediate deletion is unsafe because in-flight readers and snapshot-bound exports may still use an older snapshot. Unbounded bucket scans and process-local timers are also unsuitable for horizontally scaled installations.

## Decision

- Preserve snapshot, refresh, and export history in metadata PostgreSQL. Physical cleanup sets a nullable `purged_at` timestamp; it does not delete history or erase object identity, size, checksum, or lifecycle facts.
- Run cleanup from the existing independent scheduler process at a configurable interval. Each tick has a bounded metadata claim batch and bounded object-list page per configured backend and generated prefix.
- Claim eligible metadata rows with PostgreSQL `FOR UPDATE SKIP LOCKED` and a persisted lease timestamp. A crashed process leaves the row retryable after the lease expires; storage deletion remains idempotent.
- Never purge an active snapshot. A superseded snapshot becomes eligible only after the configured grace period, is not the dataset's active pointer, and has no queued/running or logically unexpired export reference.
- Purge a successful export artifact only after its logical expiry plus a delivery grace. Download authorization continues to return `410` after logical expiry regardless of physical cleanup timing.
- Delete the private object before recording `purged_at`. A storage failure releases the claim for retry. If deletion succeeds but the metadata transaction fails, the next idempotent delete and mark operation completes recovery.
- Reconcile orphans only under the generated `datasets/` and `exports/` prefixes. Persist one lexicographic cursor per backend and prefix, list one bounded page per tick, compare old objects with unpurged metadata references, and delete only unreferenced objects older than the configurable orphan grace.
- Keep the orphan grace substantially longer than normal publication transactions. This is the consistency barrier between an uploaded candidate and its following metadata commit.
- Audit successful metadata-object purges by resource ID and summarize orphan purges by backend, prefix, and count. Never audit object contents, credentials, endpoints, bucket names, SQL, or business values.

## Consequences

- Storage use becomes bounded over time without sacrificing operational and governance history.
- Active data and reproducible in-progress exports remain protected from cleanup.
- Multiple scheduler replicas may scan the same orphan page, but generated immutable keys and idempotent deletion keep the result safe. Metadata objects use leases to avoid duplicate normal work.
- Object listing is incremental rather than an unbounded in-memory inventory. Newly inserted keys that sort before the current cursor are discovered after the cursor reaches the end and resets.
- Disabling cleanup stops physical deletion but does not change export logical expiry or snapshot activation semantics.
- Downgrading the migration removes purge markers and cursors but cannot recreate objects already deleted.

## Alternatives considered

- **Delete the previous snapshot during publication:** rejected because in-flight readers and snapshot-bound exports may still need it.
- **Delete export metadata at expiry:** rejected because durable job history is useful for audit and operations.
- **Use only provider lifecycle rules:** rejected because providers cannot understand DatePipette metadata references, local storage, or export/snapshot protection rules.
- **List the complete bucket every tick:** rejected because cost, latency, and memory would scale with total historical objects.
- **Use Redis locks as authority:** rejected because cleanup eligibility and leases are durable metadata state.
