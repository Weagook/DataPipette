# ADR-0019: Dataset freshness projection

- Status: Accepted
- Date: 2026-10-11

## Context

Snapshot row responses already describe an active snapshot, but they cannot report the state of a dataset before its first successful publication without returning a row-query error. A client also needs a small polling resource for dataset lists and detail pages, and a queued retry must not temporarily hide the safe error that made the currently readable snapshot stale.

## Decision

- Expose `GET /api/v1/datasets/{dataset_id}/freshness` as a read-only metadata resource. It requires the same effective `view_rows` permission as detailed snapshot rows and conceals absent or inaccessible datasets as `404`.
- Derive one data state from authoritative metadata PostgreSQL: `not_published`, `refreshing`, `ready`, `stale`, or `failed`.
- Return nullable active snapshot identity, row count, activation time, active refresh-run identity and timestamps, plus the latest stable error code and completion time relevant to the current publication.
- Define `is_stale` narrowly: readable active snapshot rows exist and a later refresh failed. A failed first publication is `failed` with `is_stale=false` because there is no cached result to serve.
- Prefer a running run over its queued successor when both exist. Any queued or running run produces `data_state=refreshing`; a prior post-publication failure remains visible until a newer successful snapshot is activated.
- Use the same projection for snapshot row metadata so the dedicated status resource and returned rows cannot disagree about refresh state, staleness, or the latest safe failure.
- Do not persist the derived state or add a cache. The resource reads bounded indexed metadata and returns no SQL, source rows, raw exceptions, credentials, or storage paths.

## Consequences

- Clients can render useful initial, loading, ready, stale-fallback, and failed states without attempting a data query.
- A failed refresh continues to serve the previous snapshot while exposing a stable machine error for UI translation.
- Polling does not touch source PostgreSQL or object storage, but each request reads metadata PostgreSQL and must remain bounded.
- Error-code localization and push notifications remain client and later-delivery concerns.

## Alternatives considered

- Returning status only inside row responses was rejected because an unpublished dataset has no successful row response and list/detail screens should not run DuckDB queries merely to learn status.
- Treating every refresh failure as unavailable was rejected because it would violate stale-if-error behavior.
- Clearing the last error as soon as a retry is queued was rejected because it would hide why the active snapshot remains stale until the retry succeeds.
- Persisting a denormalized status column was deferred because the state is fully derivable from the active snapshot and bounded refresh-run lifecycle.
