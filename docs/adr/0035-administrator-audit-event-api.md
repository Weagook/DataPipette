# ADR-0035: Administrator audit-event read API

- Status: Accepted
- Date: 2026-10-14

## Context

DataPipette already writes append-only security and governance events to metadata PostgreSQL, but an administrator previously needed direct database access to inspect them. Direct access is unsuitable for the official frontend and makes external operational tooling depend on private table structure. The read surface must remain bounded, preserve the existing redaction boundary, and avoid turning an audit read into an infinite sequence of new audit records.

## Decision

- Expose `GET /api/v1/audit-events` to active administrator browser sessions only. Do not grant this installation-wide capability to service tokens, analysts, or viewers.
- Return the existing event envelope: occurrence time, actor type and identifier, action, outcome, resource type and identifier, request correlation identifier, and producer-controlled redacted details.
- Order events by `(occurred_at DESC, id DESC)` and paginate with an opaque cursor. Fetch one extra row to determine whether another bounded page exists; cap page size at 100.
- Support exact filters for actor, action, outcome, resource, and request identifier, plus an inclusive timezone-aware occurrence window. Do not support arbitrary JSONB predicates or full-text search in this slice.
- Keep audit-event reads out of the append-only audit table. The ordinary request ID and application logs remain available for operational correlation without recursive audit noise.
- Maintain the public actor vocabulary in the ORM model, including `service_account`, to match the existing database migration and domain contract.

## Consequences

- Administrators and the future UI can inspect governance history without metadata-database credentials.
- Stable keyset pagination remains reliable when newer events are appended while a caller traverses older pages.
- Event detail safety still depends on each producer recording only its explicitly redacted schema; this endpoint does not reconstruct or enrich details from sensitive data.
- Retention, export, immutable external sinks, aggregate reporting, and the administrator UI remain independent future work.

## Alternatives considered

- **Offset pagination:** rejected because concurrent appends would shift pages and produce duplicates or omissions.
- **Permit a broad audit service-token scope:** rejected because no external audit-consumer lifecycle or least-privilege scope has yet been accepted.
- **Audit every audit read:** rejected because list requests would recursively add records and create noisy, unstable pages.
- **Expose arbitrary JSONB search:** rejected because it complicates indexing and makes producer-private detail shapes part of the public query contract.
