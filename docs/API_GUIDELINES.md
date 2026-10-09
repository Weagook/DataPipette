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
/api/v1/connections
/api/v1/connections/{connection_id}
/api/v1/connections/{connection_id}/tests
/api/v1/datasets
/api/v1/datasets/{dataset_id}/draft
/api/v1/datasets/{dataset_id}/refresh-runs
/api/v1/datasets/{dataset_id}/rows:query
/api/v1/charts
/api/v1/charts/{chart_id}/data:query
/api/v1/dashboards
/api/v1/exports
/api/v1/audit-events
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

## Local user administration

- User-management endpoints require an active administrator session.
- `POST /api/v1/users` creates an active local account. Passwords are write-only and never appear in a response.
- `GET /api/v1/users` uses bounded cursor pagination; `GET /api/v1/users/{user_id}` returns one account.
- `PATCH /api/v1/users/{user_id}` changes display name, system role, or active state. Email is immutable in MVP.
- `DELETE /api/v1/users/{user_id}/sessions` revokes every active browser session and reports the number revoked without disabling the account.
- Disabling an account revokes its sessions atomically. The API rejects an operation that would remove the last active administrator.
- All state-changing operations require the session-bound CSRF cookie/header pair.

The lifecycle rationale is recorded in [ADR-0006](adr/0006-local-account-lifecycle.md).

## PostgreSQL connections

- Connection management and testing require an active administrator session; mutations are CSRF-protected.
- Password and optional CA certificate fields are write-only. Responses expose presence booleans, never secret values.
- Lists use bounded cursor pagination. Connection names are unique within an installation.
- Updating connection settings clears the previous test state until the connection is tested again.
- `POST /api/v1/connections/{connection_id}/tests` returns a safe success/failure representation. It never returns raw Psycopg, network, TLS, or database errors.
- Supported SSL modes are `disable`, `require`, `verify-ca`, and `verify-full`; verification modes require explicit CA material in the current implementation.

Secret-envelope and connection-test decisions are recorded in [ADR-0007](adr/0007-encrypted-postgresql-connections.md).

## PostgreSQL catalog

- Live catalog reads require an active administrator session until connection grants are implemented.
- `GET /api/v1/connections/{connection_id}/catalog/schemas` lists schemas for which the source role has `USAGE`. PostgreSQL system schemas are hidden by default and can be included explicitly.
- `GET /api/v1/connections/{connection_id}/catalog/schemas/{schema_name}/relations` lists selectable tables, partitioned tables, views, materialized views, and foreign tables.
- `GET /api/v1/connections/{connection_id}/catalog/schemas/{schema_name}/relations/{relation_name}` returns ordered columns, PostgreSQL-formatted data types, nullability, primary-key columns, and visible foreign-key mappings.
- Schema and relation collections use opaque cursor pagination, stable name ordering, and a maximum page size of 100.
- An absent object and an object hidden by source privileges produce the same `404 catalog_object_not_found` response. Source failures produce a redacted `502 source_catalog_unavailable` response.
- Catalog requests read the source directly and do not persist or cache source metadata.

The live-inspection boundary is recorded in [ADR-0008](adr/0008-live-postgresql-catalog.md).

## Visual dataset drafts

- Dataset draft endpoints require an active administrator session until connection grants define analyst scope; mutations are CSRF-protected.
- `POST /api/v1/datasets` creates a private dataset and its first draft for one existing PostgreSQL connection. Saving does not contact the source database.
- `GET /api/v1/datasets` uses bounded cursor pagination; `GET /api/v1/datasets/{dataset_id}` returns stable resource metadata.
- `GET /api/v1/datasets/{dataset_id}/draft` returns the complete schema-versioned visual definition and an ETag such as `"draft-3"`.
- `PUT /api/v1/datasets/{dataset_id}/draft` atomically replaces the complete definition and requires its current ETag in `If-Match`. A stale revision returns `409 dataset_revision_conflict`; a missing precondition returns `428 draft_revision_required`.
- `PATCH /api/v1/datasets/{dataset_id}` currently renames the stable dataset resource without changing its draft.
- Dataset names are unique per owner without case sensitivity. Definitions contain exactly one relation, 1–500 fields with stable UUIDs, and no joins in the first slice.
- Source validation, SQL compilation, preview, and publication are explicit later operations; a saved draft is not evidence that its source objects currently exist.

Definition storage and versioning decisions are recorded in [ADR-0009](adr/0009-versioned-visual-dataset-definitions.md).

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
- `429` for rate or concurrency limits.

## Pagination and limits

- Use cursor pagination for mutable resource collections and dataset rows.
- Never return an unbounded collection.
- Enforce server-side maximum page sizes.
- Avoid offset pagination for large snapshot row scans.
- Stable ordering must be explicit; a cursor is invalid if its snapshot is no longer active.

## Filtering and analytical queries

Complex table and chart queries use typed request bodies rather than user-written filter strings. A filter references a known column identifier, operator, and typed value. Backend compilation resolves identifiers against published schema and binds values as parameters.

## Asynchronous operations

Refresh and export creation return a job resource immediately. Job representations expose state, progress when reliable, timestamps, safe failure information, and links to related resources.

Job creation endpoints support an `Idempotency-Key` so clients can safely retry after a network failure. Idempotency is scoped to the authenticated principal and operation.

## Concurrency

Mutable resources use optimistic concurrency, such as an opaque revision or ETag. Updates based on stale state fail instead of overwriting a newer edit silently.

## Versioning and compatibility

- Backward-compatible fields and endpoints may be added within `/api/v1`.
- Existing field meaning must not change silently.
- Removed or renamed fields require a documented deprecation window.
- Breaking changes require a new major API version or an approved migration strategy.
- Generated client and contract tests must detect OpenAPI drift in CI.

## Observability

Every response includes or exposes a request identifier. Background jobs preserve correlation with the initiating request and actor. Rate-limit and retry information should use standard headers where practical.
