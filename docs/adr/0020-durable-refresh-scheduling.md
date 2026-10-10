# ADR-0020: Durable database-polled refresh scheduling

- Status: Accepted
- Date: 2026-10-11

## Context

Dataset refresh intent and execution are already durable in metadata PostgreSQL, while Redis/Celery is an at-least-once transport. Scheduled refresh must survive API, scheduler, and broker restarts; avoid publishing an unfinished draft; respect the existing one-running/one-queued bound; and interpret daily wall-clock times consistently for the installation.

## Decision

- Store one optional refresh schedule per dataset in metadata PostgreSQL. MVP cadences are a fixed interval of 5–10,080 minutes or one daily minute in the installation IANA timezone.
- Persist due timestamps and all lifecycle timestamps in UTC. Daily ambiguous DST time uses the first occurrence; a nonexistent wall time is normalized forward by the IANA timezone transition.
- Permit owners and administrators to create or replace schedules. Replacement uses an opaque ETag. Disabled schedules may exist before publication; enabling requires an active published dataset version.
- Run an independent bounded polling process. It claims due schedule rows with `FOR UPDATE SKIP LOCKED`, advances each directly to its next future occurrence, and intentionally emits at most one trigger after downtime rather than replaying every missed occurrence.
- Scheduled runs always target the active published version and use the dataset owner as `requested_by`. They never publish the separately editable draft.
- Serialize due-run creation with the dataset row. Reuse the existing queued run when present or create one queued successor when another run is active. The database partial indexes remain the final one-running/one-queued invariant.
- Mark refresh runs as `manual` or `scheduled`; scheduled runs reference the originating schedule. Celery receives only the run UUID and workers keep their existing identity, connection-scope, validation, and atomic-publication checks.
- Reconcile every queued run with no `dispatched_at` timestamp on each poll, including manual requests whose broker publication failed. Deterministic Celery task IDs and database claims make duplicate dispatch harmless.
- Disabling a schedule prevents future triggers but does not cancel queued or running work.

## Consequences

- Schedule intent and next occurrence survive process restarts without Redis becoming authoritative.
- Horizontal scheduler replicas can share work safely, although duplicate Celery publication remains possible and is handled by existing idempotent claims.
- Editing a draft cannot silently change scheduled output; a manual validated publication is required first.
- Long downtime skips intermediate occurrences, avoiding a source-query storm on recovery.
- Arbitrary cron syntax, calendars, execution retries, cancellation, and cleanup remain later capabilities.

## Alternatives considered

- Celery Beat's local schedule file was rejected because it is host-local and separate from product metadata and API concurrency.
- Scheduling the current draft was rejected because background execution could publish unreviewed edits.
- Replaying every missed occurrence was rejected because snapshots replace one active result and catch-up runs would waste source capacity.
- Sending definitions, credentials, or cadence data through Redis was rejected because workers must reload authoritative metadata by opaque identifier.
