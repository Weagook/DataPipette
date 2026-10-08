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
/api/v1/connections
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

- Browser clients use secure HTTP-only sessions.
- External systems use `Authorization: Bearer <token>`.
- Authentication identifies a principal; authorization still evaluates role, token scope, ACL, and request context.
- Cookie-authenticated state-changing requests require CSRF protection.

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
