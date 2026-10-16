# API guidelines

## Contract

The DatePipette backend is a public product surface. The official frontend and external integrations use the same API contract.

- Base path: `/api/v1`
- Transport: HTTPS in production
- Payloads: JSON unless transferring a file
- Schema: generated OpenAPI committed or verified in CI
- Client: generated TypeScript client for the official frontend
- Naming: `snake_case` JSON fields and plural resource paths
- Identifiers: opaque UUIDs represented as strings
- Time: RFC 3339 timestamps in UTC; installation timezone is returned explicitly where relevant

## Resource examples

```text
/api/v1/auth/sessions
/api/v1/auth/me
/api/v1/auth/sessions/current
/api/v1/users
/api/v1/users/{user_id}
/api/v1/users/{user_id}/sessions
/api/v1/service-accounts
/api/v1/service-accounts/{service_account_id}
/api/v1/service-accounts/{service_account_id}/tokens
/api/v1/service-accounts/{service_account_id}/tokens/{token_id}
/api/v1/groups
/api/v1/groups/{group_id}
/api/v1/groups/{group_id}/members
/api/v1/connections
/api/v1/connections/{connection_id}
/api/v1/connections/{connection_id}/tests
/api/v1/connections/{connection_id}/grants
/api/v1/connections/{connection_id}/catalog/join-suggestions
/api/v1/datasets
/api/v1/datasets/{dataset_id}/draft
/api/v1/datasets/{dataset_id}/draft/impact-analyses
/api/v1/datasets/{dataset_id}/draft/validations
/api/v1/datasets/{dataset_id}/draft/previews
/api/v1/datasets/{dataset_id}/versions
/api/v1/datasets/{dataset_id}/versions/{version_id}
/api/v1/datasets/{dataset_id}/refresh-runs
/api/v1/datasets/{dataset_id}/exploration
/api/v1/datasets/{dataset_id}/rows:query
/api/v1/datasets/{dataset_id}/grants
/api/v1/charts
/api/v1/charts/{chart_id}/data:query
/api/v1/dashboards
/api/v1/datasets/{dataset_id}/exports
/api/v1/datasets/{dataset_id}/exports/{export_id}
/api/v1/datasets/{dataset_id}/exports/{export_id}/download-link
/api/v1/export-downloads
/api/v1/audit-events
/api/v1/query-safety-policy
```

Final endpoint names must be tested for consistency before implementation. Action-style suffixes are reserved for operations that do not map cleanly to CRUD and must not spread arbitrarily.

## Authentication

- Browser clients create opaque server-side sessions with `POST /api/v1/auth/sessions`.
- Session and CSRF token plaintext exists only in the browser; metadata PostgreSQL stores SHA-256 digests.
- The `datepipette_session` cookie is HTTP-only and the `datepipette_csrf` cookie is readable by the official frontend. Both are `SameSite=Strict`; production always adds `Secure`.
- State-changing requests copy the CSRF cookie into `X-CSRF-Token`. The API verifies the cookie, header, and digest bound to the authenticated session.
- `GET /api/v1/auth/me` resolves the current principal. `DELETE /api/v1/auth/sessions/current` revokes the session.
- External systems use `Authorization: Bearer <token>`.
- Authentication identifies a principal; authorization still evaluates role, token scope, ACL, and request context.
- Invalid and unknown credentials return the same public error. Redis limits attempts by normalized account and client address.

The durable rationale and consequences are recorded in [ADR-0005](adr/0005-server-side-browser-sessions.md).

## Service accounts and API tokens

- Administrator browser sessions create, list, read, and update service accounts at `/api/v1/service-accounts`. Mutations require CSRF. Service accounts may be `viewer` or `analyst`, never `admin`, and do not have passwords or browser sessions.
- `POST /api/v1/service-accounts/{service_account_id}/tokens` creates an independently revocable token with a name, non-empty scope set, and optional future RFC 3339 expiry. The `dpp_...` bearer secret appears only in this response.
- `GET /api/v1/service-accounts/{service_account_id}/tokens` returns metadata, scopes, expiry, revocation, creation, and last-use timestamps but never token plaintext or hashes. `DELETE .../{token_id}` revokes one token immediately.
- The initial closed scope is `dashboards:read`. A bearer with that scope may call dashboard list/read and saved-item query endpoints only. Effective dashboard user/group grants and the service account's active state are still evaluated on every request.
- A bearer token cannot manage its account, tokens, grants, layouts, users, datasets, exports, source connections, or direct charts. Unknown, malformed, expired, revoked, and disabled-account tokens return the same safe `401`; a valid token missing the route scope returns `403`.
- Multiple live tokens support rotation. Non-expiring tokens are allowed but should be used only when an external secret-rotation process exists.

The token lifecycle and authorization rationale is recorded in [ADR-0033](adr/0033-service-account-api-tokens.md).

## Audit events

- `GET /api/v1/audit-events` requires an active administrator browser session. Service tokens and non-administrator sessions cannot read the installation-wide governance history.
- Results use newest-first opaque cursor pagination ordered by `(occurred_at DESC, id DESC)`. Page size defaults to 50 and is capped at 100.
- Exact filters are composable for actor type and ID, action, outcome, resource type and ID, and request ID. Inclusive `occurred_from` and `occurred_to` filters require timezone-aware RFC 3339 values.
- Responses contain stable identifiers and the redacted structured metadata already stored by event producers. They never reconstruct credentials, API-token plaintext, connection secrets, raw exceptions, SQL parameters, or business rows.
- Reading the log does not itself create an audit event, which avoids recursive read noise. Existing audit events remain append-only and have no public mutation endpoint.

The read boundary and pagination decision is recorded in [ADR-0035](adr/0035-administrator-audit-event-api.md).

## Local user administration

- User-management endpoints require an active administrator session.
- `POST /api/v1/users` creates an active local account. Passwords are write-only and never appear in a response.
- `GET /api/v1/users` uses bounded cursor pagination; `GET /api/v1/users/{user_id}` returns one account.
- `PATCH /api/v1/users/{user_id}` changes display name, system role, or active state. Email is immutable in MVP.
- `DELETE /api/v1/users/{user_id}/sessions` revokes every active browser session and reports the number revoked without disabling the account.
- Disabling an account revokes its sessions atomically. The API rejects an operation that would remove the last active administrator.
- All state-changing operations require the session-bound CSRF cookie/header pair.

The lifecycle rationale is recorded in [ADR-0006](adr/0006-local-account-lifecycle.md).

## Groups and dataset grants

- Group creation, listing, renaming, membership listing, and idempotent membership addition/removal require an active administrator; mutations require CSRF.
- Group names are whitespace-normalized, case-insensitively unique, and contain at most 100 characters.
- Dataset grants target one active analyst or one group and contain `view_rows`, `export`, or `manage`. Repeating an identical grant returns the existing grant.
- Administrators and dataset owners have all dataset permissions. Active analysts receive the union of direct and group grants. The `Viewer` role cannot browse detailed rows, export them, or manage grants even through a group.
- `GET`, `POST`, and `DELETE /api/v1/datasets/{dataset_id}/grants...` require `manage`; mutation requests require CSRF. A `manage` holder may delegate access.
- Grant and membership changes are read from metadata PostgreSQL on the next protected request. Missing and inaccessible datasets are both represented as `404 dataset_not_found`.

The authorization model is recorded in [ADR-0016](adr/0016-additive-dataset-access-control.md).

## PostgreSQL connections

- Connection creation, configuration, and testing require an active administrator session; mutations are CSRF-protected.
- Password and optional CA certificate fields are write-only. Responses expose presence booleans, never secret values.
- Lists use bounded cursor pagination. Connection names are unique within an installation. Administrators list every connection, active analysts list only connections with an effective direct or group grant, and viewers receive an empty collection.
- Updating connection settings clears the previous test state until the connection is tested again.
- `POST /api/v1/connections/{connection_id}/tests` returns a safe success/failure representation. It never returns raw Psycopg, network, TLS, or database errors.
- Connection grants are administrator-managed and target one active analyst or one group. Exact scopes are `all`, `schema`, and `table`; repeating an identical grant returns the existing grant.
- Scope unions are additive. `Viewer` remains unable to use a connection even through a granted group. Grant and membership changes affect the next request because effective scope is not cached.
- Grant mutations require CSRF and append redacted audit events containing identifiers and scope names only.
- Supported SSL modes are `disable`, `require`, `verify-ca`, and `verify-full`; verification modes require explicit CA material in the current implementation.

Secret-envelope and connection-test decisions are recorded in [ADR-0007](adr/0007-encrypted-postgresql-connections.md).

## PostgreSQL catalog

- Live catalog reads require an administrator or an active analyst with effective connection access. An absent or inaccessible connection is represented as `404 connection_not_found`.
- `GET /api/v1/connections/{connection_id}/catalog/schemas` lists schemas for which the source role has `USAGE`. PostgreSQL system schemas are hidden by default and can be included explicitly.
- `GET /api/v1/connections/{connection_id}/catalog/schemas/{schema_name}/relations` lists selectable tables, partitioned tables, views, materialized views, and foreign tables.
- `GET /api/v1/connections/{connection_id}/catalog/schemas/{schema_name}/relations/{relation_name}` returns ordered columns, PostgreSQL-formatted data types, nullability, primary-key columns, and visible foreign-key mappings.
- `POST /api/v1/connections/{connection_id}/catalog/join-suggestions` is a read-only structured catalog query over 1–10 caller-identified relations. It returns foreign keys only between requested authorized relations, preserves composite-key column order, and never changes a dataset.
- Schema and relation collections use opaque cursor pagination, stable name ordering, and a maximum page size of 100.
- Analyst scope filtering happens in the source query before pagination. A table grant exposes its containing schema but no sibling relations; foreign-key mappings omit targets outside effective scope.
- An absent object and an object hidden by source privileges produce the same `404 catalog_object_not_found` response. Source failures produce a redacted `502 source_catalog_unavailable` response.
- Catalog requests read the source directly and do not persist or cache source metadata.

The live-inspection boundary is recorded in [ADR-0008](adr/0008-live-postgresql-catalog.md); DatePipette scope enforcement is recorded in [ADR-0017](adr/0017-scoped-connection-access.md).

## Dataset drafts

- Dataset draft endpoints require an administrator or the active analyst who owns the dataset; mutations are CSRF-protected. Other analysts and viewers receive the same `404 dataset_not_found` boundary as an absent dataset.
- `POST /api/v1/datasets` creates a private dataset and its first draft on relations currently allowed by the actor's connection scope. It stores the structured definition without contacting the source database.
- `GET /api/v1/datasets` uses bounded cursor pagination; administrators see every dataset and analysts see only their own. `GET /api/v1/datasets/{dataset_id}` returns stable resource metadata, including nullable `active_version_id` and `active_snapshot_id`.
- `GET /api/v1/datasets/{dataset_id}/draft` returns the complete schema-versioned visual or SQL definition, its latest revision-bound validation artifact when one exists, and an ETag such as `"draft-3"`.
- `GET /api/v1/datasets/{dataset_id}/versions` returns bounded newest-first cursor pages of immutable published/superseded versions. `GET .../versions/{version_id}` returns one complete historical definition and validation artifact; mutable draft identifiers return the same `404 dataset_not_found` boundary as absent versions.
- `POST /api/v1/datasets/{dataset_id}/draft/impact-analyses` requires CSRF and the current draft ETag. It performs no source query: it compares the current revision-bound validation schema with the active published version by stable field UUID and returns compatible/breaking field changes plus currently empty dependency-impact collections.
- Impact reports classify removal, rename, type change, newly nullable output, and an unavailable defined field as breaking. Addition, newly required output, display changes, and source remapping with a stable output contract are compatible. An invalid validation can be explained but returns `ready_for_publication: false`; a missing/stale validation returns `409 dataset_draft_not_validated`.
- `PUT /api/v1/datasets/{dataset_id}/draft` atomically replaces the complete definition and requires its current ETag in `If-Match`. A stale revision returns `409 dataset_revision_conflict`; a missing precondition returns `428 draft_revision_required`.
- `POST /api/v1/datasets/{dataset_id}/draft/validations` requires the current draft ETag and explicitly re-checks every live relation. Visual definitions also check selected and join columns; SQL definitions execute a zero-row wrapper and require their declared field names to match result-column names and order. The endpoint returns `valid`, `valid_with_warnings`, `blocked`, or `invalid`, the PostgreSQL-formatted result schema, safely compiled SQL, a compact planner summary, visible relation statistics, stable findings with observed/threshold evidence, and typed review recommendations. Recommendations contain no executable DDL; only visual join metadata can supply candidate index columns.
- Missing source objects are `200` validation results with error findings. A network, TLS, credential, timeout, or unexpected source response returns the redacted `502 source_validation_unavailable` problem and does not erase the previous artifact.
- Validation uses `EXPLAIN (FORMAT JSON)` without `ANALYZE`; no business rows are executed, previewed, or stored. The revisioned metadata policy can warn or block on estimated output rows, planner cost, or join count; large sequential scans/sorts, absent visible indexes, and stale statistics remain explainable advisory findings.
- Active administrators read and completely replace planner thresholds plus source, interactive, export, and background-concurrency runtime budgets at `/api/v1/query-safety-policy`. Replacement requires CSRF and the current `"query-safety-policy-N"` ETag. With no stored row, validated installation defaults are exposed as revision zero; the first replacement creates revision one and is audited.
- A blocked validation may be repeated with `{ "override_reason": "..." }` only by an administrator. The fresh plan must still contain a blocking finding. The resulting artifact is `valid_with_warnings`, records the policy revision, actor, reason, time, and overridden codes, and creates a separate audit event. Analysts cannot override.
- The managed policy narrows synchronous preview by statement timeout, requested rows, and encoded response bytes. It bounds background refresh by statement timeout and source result rows. Preview resolves the policy after resource authorization on every request and returns the applied `policy_revision`; each Celery refresh job resolves it before opening the source. A change affects new operations without restarting API or worker processes, while an already-running operation retains the revision captured at its start.
- The preview policy cannot expand the hard API ceilings of 200 rows or one MiB. A request over the current policy returns `422 preview_policy_limit_exceeded`.
- The same managed policy bounds interactive DuckDB table, chart, and dashboard-item queries by timeout, encoded response bytes, memory, threads, temporary disk, and installation-wide concurrency. Each authorized request resolves one revision after active-snapshot lookup; the captured values govern admission and execution, and successful response metadata reports `policy_revision`. All API instances share metadata-PostgreSQL advisory slots. Exhausted capacity fails immediately with `503 interactive_query_capacity_exceeded` and `Retry-After: 1`; metadata-database admission failure uses the existing safe query-unavailable response.
- The managed policy also bounds export candidate generation by timeout, artifact bytes, rows, memory, threads, and temporary disk. One `background_job_concurrency` value admits refresh and export attempts through shared metadata-PostgreSQL advisory slots across worker processes and hosts. Capacity deferral leaves the job queued and causes Celery redelivery; it does not consume an attempt. Refresh and export resources expose the revision captured when an attempt starts.
- Replacing a definition clears its validation artifact. A concurrent edit during validation produces `409 dataset_revision_conflict` rather than attaching stale derived metadata.
- `POST /api/v1/datasets/{dataset_id}/draft/previews` synchronously returns at most 200 rows from the exact current draft revision. It requires the current ETag, CSRF, and a `valid` or `valid_with_warnings` artifact for that revision.
- Preview rebuilds execution SQL from the authoritative definition document rather than executing a stored validation artifact. It binds a server-side `LIMIT`, streams through a named source cursor, and applies the current policy's statement timeout and encoded-row budget. `truncated` and `truncation_reason` distinguish `row_limit` and `byte_limit` from a complete result.
- Preview order is unspecified until an explicit sort is added. Repeated requests may return a different subset even when the source data is unchanged.
- JSON preview values preserve `null`, booleans, integers, finite floats, strings, arrays, and objects. PostgreSQL `numeric`/`decimal` becomes a string to avoid precision loss; dates/times use ISO text; UUID and interval values use text; `bytea` uses base64.
- Source preview failure returns redacted `502 source_preview_unavailable`. A concurrent edit discards the fetched rows and returns `409 dataset_revision_conflict`.
- `PATCH /api/v1/datasets/{dataset_id}` currently renames the stable dataset resource without changing its draft.
- Dataset names are unique per owner without case sensitivity. Schema version 1 remains exactly one relation with no joins. Schema version 2 contains 1–10 uniquely aliased relations, 1–500 fields, and a deterministic rooted tree of explicit `inner` or `left` joins. Each additional relation is introduced once with 1–8 equality column pairs; cycles, disconnected graphs, cross joins, expressions, and arbitrary predicates are rejected.
- An SQL definition uses `kind: sql`, `schema_version: 1`, one statement of at most 65,536 UTF-8 bytes, and 1–500 output fields with stable UUIDs, names, and display settings. The API preserves the trimmed statement for editor round-trips. It accepts one PostgreSQL-dialect `SELECT`/query expression with optional read-only CTEs and rejects multiple statements, DDL/DML, data-modifying CTEs, `SELECT INTO`, row locks, external parameters, unqualified relations, and cross-database names with `422 dataset_definition_invalid`.
- SQL validation reports inaccessible/missing relations as `sql_source_relation_not_found`, PostgreSQL semantic errors as `sql_query_invalid`, and output-column drift as `sql_result_schema_mismatch`. SQL field nullability is conservatively reported as nullable in this version.
- Create and draft replacement re-check source authorization for every referenced relation but otherwise remain offline and are not evidence that source objects exist. Validation is explicit and does not imply that preview, refresh, or publication has succeeded. Validation and preview re-check both ownership and the complete current relation scope before touching the source.

Definition storage and versioning decisions are recorded in [ADR-0009](adr/0009-versioned-visual-dataset-definitions.md); the live-validation boundary is recorded in [ADR-0010](adr/0010-revision-bound-dataset-validation.md); bounded preview execution is recorded in [ADR-0011](adr/0011-bounded-dataset-draft-preview.md); analyst ownership is recorded in [ADR-0017](adr/0017-scoped-connection-access.md); the version-2 join model is recorded in [ADR-0018](adr/0018-versioned-visual-join-trees.md); advanced SQL is recorded in [ADR-0021](adr/0021-advanced-read-only-sql-datasets.md); version history and schema impact are recorded in [ADR-0025](adr/0025-dataset-version-history-and-schema-impact.md); managed planner policy and override governance are recorded in [ADR-0036](adr/0036-managed-query-safety-policy.md); source runtime budgets are recorded in [ADR-0037](adr/0037-managed-source-runtime-budgets.md).

## Refresh runs

- `GET /api/v1/datasets/{dataset_id}/refresh-schedule` returns the owner/admin schedule and its ETag. `PUT` creates a schedule without `If-Match` or atomically replaces an existing schedule with its current ETag; missing update preconditions return `428 schedule_revision_required`, while stale revisions return `409 schedule_revision_conflict`.
- A schedule is either `interval` with 5–10,080 minutes or `daily` with a timezone-naive `HH:MM` value interpreted in the returned installation `timezone`. Disabled schedules have `next_run_at: null`. Enabling requires an active published dataset and returns `409 dataset_not_published` otherwise.
- Scheduled runs target the active published version, not the editable draft. Refresh-run resources expose `trigger_type` (`manual` or `scheduled`) and nullable `schedule_id`.
- `POST /api/v1/datasets/{dataset_id}/refresh-runs` requires administrator or owner CSRF, the current draft ETag in `If-Match`, and an `Idempotency-Key` of at most 200 characters. The exact revision must have a current `valid` or `valid_with_warnings` validation artifact and the actor must still have source-relation access.
- A refresh run captures `planner_estimated_rows` from that validation when an attempt starts. Terminal attempts expose `execution_duration_ms`, `actual_rows` when materialization produced a candidate snapshot, and `row_estimate_ratio` when a positive estimate and actual count are both available. These values come from the requested refresh itself; DatePipette does not run `EXPLAIN ANALYZE` or a separate count query.
- A successful request returns `202`, the durable run resource, `Location`, the draft `ETag`, and `X-DatePipette-Coalesced`. The latter is `true` when this request reused an existing run.
- Idempotency is scoped to actor and dataset. Retrying the same key returns the same run. A different key coalesces into the one queued run when possible; metadata PostgreSQL retains each hashed request mapping.
- At most one run is `running` and one is `queued` per dataset. A queued successor waits when another run is active. Duplicate Celery delivery does not restart a running or terminal run.
- If Redis publication fails, the API returns `503 refresh_dispatch_unavailable` with `Location` and `Retry-After`. Retrying with the same key republishes the same saved run.
- `GET /api/v1/datasets/{dataset_id}/refresh-runs/{run_id}` returns durable state, dispatch and execution attempt counts, timestamps, nullable `snapshot_id`, and a stable safe `error_code`.
- A successful run has a completely written and validated Parquet object before `snapshot_id` is exposed. Execution streams fixed-size source batches and atomically activates snapshot metadata; it never buffers the complete result. A refresh that would exceed the current row budget fails with `result_row_limit_exceeded`, removes its local candidate, publishes no object, and leaves the previous snapshot active.
- The worker re-checks the requesting user's current source-relation scope immediately before source access. Stable execution errors currently include `connection_access_revoked`, `draft_changed`, `result_row_limit_exceeded`, `unsupported_source_type`, `connection_secret_unavailable`, `source_unavailable`, `snapshot_write_failed`, and `refresh_execution_failed`. A failure leaves the previous active snapshot unchanged.

The durable orchestration boundary is recorded in [ADR-0012](adr/0012-durable-refresh-run-orchestration.md); local Parquet publication is recorded in [ADR-0013](adr/0013-local-parquet-snapshot-publication.md); scheduling is recorded in [ADR-0020](adr/0020-durable-refresh-scheduling.md).

## Snapshot row queries

- `GET /api/v1/datasets/{dataset_id}/freshness` is a read-only metadata operation available before the first publication. It requires the same effective `view_rows` permission as detailed rows and returns `not_published`, `refreshing`, `ready`, `stale`, or `failed`, nullable snapshot/run identities and timestamps, current refresh state, and the latest relevant safe error. It never reads source PostgreSQL, Parquet, or DuckDB.
- `is_stale` is true only when an active snapshot remains readable after a later failure. A queued/running retry reports `refreshing` but retains that error and staleness until a newer snapshot succeeds. A failed first publication reports `failed` with no snapshot and `is_stale=false`.
- `GET /api/v1/datasets/{dataset_id}/exploration` requires effective `view_rows` and an active snapshot. It returns the safe dataset display name, whether current access includes `export`, the installation IANA timezone, active published field order, UUIDs, names, labels, PostgreSQL types, nullability, visibility, display formats, sortable flag, accepted filter operators, a visible-field default projection bounded to the 100-field query limit, snapshot identity/activation/row count, and freshness/error metadata. It reads only metadata PostgreSQL; a draft edit cannot affect the descriptor until successful publication.
- `POST /api/v1/datasets/{dataset_id}/rows:query` is a read-only operation with a structured body. It requires an authenticated administrator, owner, or active analyst with `view_rows`; the viewer role is denied. It does not require CSRF.
- `fields` contains 1–100 published field UUIDs. A request may contain at most 20 filters, five sort fields, 100 values in one `in` filter, and 200 rows per page.
- Supported operators are `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `between`, `in`, `contains`, `starts_with`, `is_null`, and `is_not_null`. Operators are checked against the published PostgreSQL type. Comparisons apply to integers, bounded decimals, floating-point values, and temporal fields; unconstrained `numeric` remains exact text in the current Parquet format. Use null operators instead of `eq: null`.
- Excel-style semicolon input is translated by a client into an explicit `in` value array. The API does not accept or parse filter-expression strings.
- Field UUIDs resolve to generated physical Parquet columns. Filter and cursor values are parameter-bound; callers cannot submit identifiers, expressions, or SQL.
- Sorts use `NULLS LAST`. The immutable Parquet file row number is the final internal ascending tie-breaker, including when no user sort is supplied.
- `cursor` is encrypted, authenticated, and bound to dataset, active snapshot, projection, normalized filters, and sorting. A changed query or newly active snapshot returns `400 invalid_snapshot_cursor`.
- Responses enforce the current policy's encoded-row budget, defaulting to two MiB. `truncated_by_bytes` can be true before `page_size` is reached; `next_cursor` continues after the last returned row. A single row larger than the limit returns `413 snapshot_row_too_large`. Selected-column metadata repeats the published label, visibility, and display format so each page is self-describing.
- Metadata includes policy revision, snapshot ID, total rows in the unfiltered snapshot, `last_success_at`, current refresh state, staleness, the last safe refresh error and its timestamp, and the next cursor. It intentionally omits an expensive filtered total count.
- Missing publication returns `409 dataset_snapshot_not_published`; missing, corrupt, or unavailable local/S3 data returns redacted `503 snapshot_query_unavailable`; timeout returns `504 snapshot_query_timeout`.
- Decimal values are JSON strings, temporal values are ISO text normalized to UTC when timezone-aware, binary values are base64, and JSON snapshot fields are restored as JSON values.

The query and cursor rationale is recorded in [ADR-0014](adr/0014-typed-snapshot-row-queries.md); the shared freshness projection is recorded in [ADR-0019](adr/0019-dataset-freshness-projection.md); the published exploration projection is recorded in [ADR-0027](adr/0027-published-dataset-exploration-contract.md).

## Dataset exports

- `POST /api/v1/datasets/{dataset_id}/exports` requires CSRF, an `Idempotency-Key`, and both `view_rows` and `export` dataset permissions. It accepts `csv` or `xlsx` plus the same bounded field UUID, typed filter, and sort vocabulary used by snapshot row queries; cursors and page size do not apply.
- Creation returns `202`, `Location`, `X-DatePipette-Idempotent-Replay`, and a durable job bound to the active snapshot at request time. Retrying the same actor/dataset/key returns the same job.
- `GET /api/v1/datasets/{dataset_id}/exports` returns at most 100 newest jobs; `GET .../{export_id}` returns durable state, applied policy revision, safe error code, timestamps, row count, bytes, checksum, and logical expiry. Non-administrators with `export` see only their own jobs.
- `POST .../{export_id}/download-link` requires `export` and, for non-administrators, ownership of that export job. It returns a relative five-minute capability URL only for a successful unexpired job and may be called repeatedly without regenerating the artifact.
- `GET /api/v1/export-downloads?token=...` requires the encrypted capability rather than a session. It returns a private local file or a `307` redirect to an equally short-lived presigned private S3-compatible URL with attachment metadata. Invalid capabilities are concealed as `404`; expired exports return `410`; unavailable or corrupt storage returns a redacted `503`.
- Jobs use `queued`, `running`, `succeeded`, and `failed`. `export_timeout`, `export_limit_exceeded`, and `export_generation_failed` are stable safe worker errors; failures never affect the active dataset snapshot.

The export lifecycle and security boundary are recorded in [ADR-0015](adr/0015-snapshot-bound-background-exports.md). The shared storage and presigned-download decision is recorded in [ADR-0022](adr/0022-shared-s3-compatible-object-storage.md).

## Success responses

Ordinary resources use direct JSON objects. Collection responses include data and pagination metadata:

```json
{
  "data": [],
  "meta": {
    "next_cursor": null
  }
}
```

Dataset result pages include snapshot and freshness metadata separately from rows:

```json
{
  "data": {
    "columns": [],
    "rows": []
  },
  "meta": {
    "snapshot_id": "opaque-id",
    "snapshot_state": "ready",
    "is_stale": false,
    "last_success_at": "2026-10-08T08:00:00Z",
    "refresh_state": "idle",
    "last_refresh_error": null,
    "next_cursor": null
  }
}
```

Errors must never be encoded as a successful empty result.

## Error responses

Use `application/problem+json` following Problem Details semantics. Each error has a stable machine-readable code in addition to the HTTP status.

```json
{
  "type": "https://docs.datepipette.dev/problems/dataset-invalid",
  "title": "Dataset is invalid",
  "status": 409,
  "code": "dataset_invalid",
  "detail": "The published dataset no longer provides column 'revenue'.",
  "request_id": "opaque-id",
  "errors": []
}
```

Do not expose stack traces, SQL credentials, API token material, internal storage paths, or unredacted source errors.

## HTTP semantics

- `200` for successful reads and synchronous updates with a response body.
- `201` for newly created resources.
- `202` for accepted asynchronous jobs, with job representation and `Location` where useful.
- `204` for successful operations with no response body.
- `400` for malformed request semantics not covered by field validation.
- `401` when authentication is absent or invalid.
- `403` when the principal is authenticated but not authorized.
- `404` when a resource is absent or intentionally concealed from the principal.
- `409` for state conflicts, including blocked breaking publication.
- `422` for structured validation errors.
- `429` for caller-specific rate or quota limits.
- `503` for temporary installation capacity exhaustion or a required unavailable dependency; retryable interactive capacity responses include `Retry-After`.

## Pagination and limits

- Use cursor pagination for mutable resource collections and dataset rows.
- Never return an unbounded collection.
- Enforce server-side maximum page sizes.
- Avoid offset pagination for large snapshot row scans.
- Stable ordering must be explicit; a cursor is invalid if its snapshot is no longer active.

## Filtering and analytical queries

Complex table and chart queries use typed request bodies rather than user-written filter strings. A filter references a known column identifier, operator, and typed value. Backend compilation resolves identifiers against published schema and binds values as parameters.

## Charts

- `POST /api/v1/charts` creates a private chart for an active published dataset; `GET /api/v1/charts` uses bounded cursor pagination and returns owned charts to analysts or all charts to administrators.
- `GET /api/v1/charts/{chart_id}` returns the saved schema-versioned definition. `PUT` replaces the complete definition and requires the current `"chart-N"` ETag in `If-Match`.
- Creation and replacement require CSRF, chart ownership (or administrator role), current effective dataset `view_rows`, and validation against the active published field contract. A chart never grants dataset access.
- `POST /api/v1/charts/{chart_id}/data:query` executes only the saved definition against the current active Parquet snapshot. It returns stable result keys, column roles and identifiers, chart revision, snapshot identity, freshness, stale error metadata, and response truncation state.
- Chart definition version 2 adds an optional dimension `time_bucket` of `hour`, `day`, `week`, `month`, `quarter`, or `year`. Bucketed timestamp result columns include the installation timezone; version-1 definitions remain valid without buckets.
- Direct chart querying is owner/administrator-only in this slice. Dashboard-context viewer access is a separate future authorization path and will never accept arbitrary chart definitions.
- Deletion is intentionally absent until chart archival and dashboard dependency semantics are accepted.

The rationale and bounds are recorded in [ADR-0028](adr/0028-private-chart-query-foundation.md) and [ADR-0029](adr/0029-timezone-aware-chart-time-buckets.md).

## Dashboards

- `POST /api/v1/dashboards` creates a private dashboard; `GET /api/v1/dashboards` uses bounded cursor pagination and returns owned or explicitly shared dashboards, while administrators see all dashboards.
- `GET /api/v1/dashboards/{dashboard_id}` returns the complete editable definition. `PUT` replaces the normalized name and complete definition and requires the current `"dashboard-N"` ETag in `If-Match`; a no-op replacement does not advance the revision.
- Definition version 1 uses a fixed 12-column grid with stable item and chart UUIDs plus integer `x`, `y`, `width`, and `height`. Up to 50 items are allowed; identifiers and chart references must be unique, geometry must remain in bounds, and rectangles must not overlap.
- A dashboard may reference owned charts from different datasets, but it does not query, join, or calculate across those datasets. Every reference is validated and persisted as a normalized dependency in the same transaction as the JSONB definition.
- `POST /api/v1/dashboards/{dashboard_id}/items/{item_id}/data:query` resolves the chart only from the current saved item and executes it through the existing bounded chart-query path. Its metadata includes the dashboard ID/revision, item ID, saved chart name/type, and chart, dataset, snapshot, and freshness fields. Presentation metadata lets constrained Viewers and headless clients render the saved visualization without direct chart-resource access.
- Clients issue one request per item so loading, cancellation, retry, and safe errors remain independent. There is no bulk fan-out response and no request field that accepts a chart ID or arbitrary definition.
- `GET|POST /api/v1/dashboards/{dashboard_id}/grants` and `DELETE /api/v1/dashboards/{dashboard_id}/grants/{grant_id}` manage additive account/group view grants. The existing `user` principal type represents a direct account identifier and accepts an active human or service account; group grants also apply through current service-account membership. Grant writes require CSRF and remain owner/administrator-only.
- Shared active users, including `Viewer`, may read the saved layout and query only its saved aggregate items. They cannot replace the layout, manage grants, access dataset rows or exports, discover source metadata, or call direct chart resources through this permission. Unauthorized reads remain concealed as `404`.
- Deletion and archival are intentionally absent until lifecycle behavior for referenced charts is accepted.

The persistence and execution rationale is recorded in [ADR-0030](adr/0030-private-dashboard-layouts.md), [ADR-0031](adr/0031-independent-dashboard-item-queries.md), and [ADR-0032](adr/0032-dashboard-sharing-and-viewer-execution.md).

## Asynchronous operations

Refresh and export creation return a job resource immediately. Job representations expose state, progress when reliable, timestamps, safe failure information, and links to related resources.

Job creation endpoints support an `Idempotency-Key` so clients can safely retry after a network failure. Idempotency is scoped to the authenticated principal and operation.

Refresh cancellation is an explicit command on the job resource. A queued run becomes `cancelled` immediately; a running run first exposes `cancel_requested_at` and becomes `cancelled` when cooperative execution stops. Terminal success and failure cannot be rewritten by cancellation.

## Concurrency

Mutable resources use optimistic concurrency, such as an opaque revision or ETag. Updates based on stale state fail instead of overwriting a newer edit silently.

## Versioning and compatibility

- Backward-compatible fields and endpoints may be added within `/api/v1`.
- Existing field meaning must not change silently.
- Removed or renamed fields require a documented deprecation window.
- Breaking changes require a new major API version or an approved migration strategy.
- Generated client and contract tests must detect OpenAPI drift in CI.

## Generated TypeScript client

- `clients/typescript` is generated from the committed `docs/openapi.json`; generated request/response types are not edited or duplicated by hand.
- `npm run generate:check --prefix clients/typescript` regenerates into a temporary location and fails on byte-level drift. Any intentional API change commits both OpenAPI and the reviewed TypeScript schema.
- Browser and bearer constructors are separate. Browser mode includes cookies and keeps CSRF explicit; bearer mode accepts one caller-owned secret and never falls back to cookies.
- `DatePipetteApiError` preserves safe Problem Details and `X-Request-ID`. The client does not expose raw backend failures, persist credentials, retry mutations, or weaken endpoint authorization.
- The package is independently buildable and currently private to the monorepo. npm publication and semantic-version release automation require a later compatibility decision.

The generation boundary is recorded in [ADR-0034](adr/0034-generated-typescript-api-client.md).

## Observability

Every response includes or exposes a request identifier. Background jobs preserve correlation with the initiating request and actor. Rate-limit and retry information should use standard headers where practical.
