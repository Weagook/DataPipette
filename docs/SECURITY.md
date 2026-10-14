# Security model

## Scope

This document describes the intended security architecture. Vulnerability reporting instructions are in the repository root [SECURITY.md](../SECURITY.md).

## Trust boundaries

```text
Browser / external client
        | untrusted input
        v
DatePipette API
        | authenticated and authorized operations
        +--> Metadata PostgreSQL
        +--> Redis
        +--> Private S3-compatible storage
        +--> Celery workers
                 |
                 v
          Source PostgreSQL
```

Source databases, SQL text, filter values, filenames, object metadata, API clients, and browser input are all treated as untrusted.

## Authentication

### Human users

- MVP uses local accounts.
- Passwords are stored with Argon2id and a per-password salt.
- Browser sessions are opaque, server-side, revocable, and stored only as SHA-256 token digests.
- Session cookies use `HttpOnly` and `SameSite=Strict`; production forces `Secure`.
- State-changing cookie requests use a session-bound double-submit CSRF token.
- Redis limits login attempts independently by normalized account and client address.
- Successful and failed login activity is audited without recording passwords or token plaintext.
- User administration requires an active administrator session and CSRF validation for mutations.
- Disabling an account atomically revokes its sessions; supported APIs preserve at least one active administrator.
- Local accounts are disabled rather than deleted while ownership-transfer rules remain unresolved.
- OIDC is deferred but identity-provider linkage must remain separable from domain authorization.

See [ADR-0005](adr/0005-server-side-browser-sessions.md) for the implemented browser-session design and [ADR-0006](adr/0006-local-account-lifecycle.md) for account lifecycle rules.

### Service accounts

- Each external integration uses a distinct service account.
- Service accounts have no password or browser session, cannot be administrators, and are disabled independently of human accounts.
- API token plaintext is returned once, carries a `dpp_` prefix, and is stored only as a SHA-256 digest. Tokens may expire or remain non-expiring, are independently revocable, and record last successful use.
- Bearer authentication reloads token expiry/revocation, account status/role, closed scopes, and current object/group grants on every request. The initial `dashboards:read` scope reaches only dashboard list/read and saved-item query endpoints.
- A token scope narrows but never supplies object access. An active service account still needs an effective dashboard grant, and bearer tokens are not accepted by administration, authoring, row, export, source, refresh, grant-management, or direct-chart routes.
- Token plaintext is shown once and only a secure hash and non-secret identifier are retained.
- Tokens may be expiring or non-expiring, are revocable, and support overlapping rotation.
- Token scopes may only narrow service-account permissions.
- Logs and traces must redact authorization headers and token-like values. API responses and audit events never contain token plaintext after the one-time creation response.

## Authorization

Authorization combines:

1. principal status;
2. system role;
3. token scopes when applicable (currently only `dashboards:read`);
4. direct and group ACL grants;
5. resource state; and
6. request context.

New resources are private to their owner and administrators. Grants are additive in MVP. Direct dataset access is distinct from dashboard-context access to aggregated chart results.

Dataset authorization is evaluated from metadata PostgreSQL for every protected request. Administrators and dataset owners receive all dataset permissions. Active analysts receive the union of their direct grants and group grants. `view_rows`, `export`, and `manage` are separate; export creation requires both `view_rows` and `export`, while `manage` permits grant delegation. The `Viewer` role is a hard ceiling and cannot be raised to detailed-row, export, or dataset-grant access through group membership. Grant and membership revocation therefore affects the next request without waiting for a permission cache.

Dataset-version history and schema-impact analysis use the private owner/administrator authoring boundary because version definitions and validation artifacts may contain source identifiers and generated SQL. Impact analysis additionally re-checks current connection scope, requires CSRF plus the current draft ETag, executes no source query, and audits only revision, state, counts, and stable change codes.

Connection access is evaluated from metadata PostgreSQL on every source-touching boundary. Administrators bypass grants; active analysts receive the union of exact `all`, `schema`, and `table` scopes granted directly or through current group membership; viewers are always denied. A draft records its owner immediately and remains readable/editable only by that owner and administrators. Create and replacement require current relation access; validation, preview, refresh request, and worker execution re-check it before source access.

Refresh requests are owner-or-administrator and CSRF-protected. They require the current draft ETag and store only a SHA-256 digest of the client idempotency key. Celery messages contain only a refresh-run UUID; definitions, SQL, connection credentials, business rows, and idempotency-key plaintext remain outside Redis. The worker reloads the requesting identity and connection scope, so a queued job cannot outlive grant or membership revocation. Metadata PostgreSQL is authoritative for lifecycle state, and audit events use stable safe error codes rather than raw broker, driver, or source errors.

Refresh schedules are likewise owner-or-administrator and CSRF-protected, use optimistic ETags, and never carry source material through Redis. Scheduled runs target the active published version and execute as the dataset owner, so worker-time account and connection-scope revocation still applies. Scheduler audit events contain only dataset, schedule, run, cadence, revision, and dispatch metadata.

Local Parquet files contain business data and therefore belong under a private runtime directory or volume, never the repository or a publicly served path. Metadata and audit events store only relative object keys, storage backend, schema/type metadata, identifiers, counts, checksums, and safe error codes—not row values, storage credentials, endpoints, bucket names, or host-specific absolute paths. Production requires the private S3-compatible adapter; custom production endpoints must use HTTPS, and deployments must use least-privilege credentials.

A viewer with dashboard access may receive chart results but must not use that route to browse dataset rows, create exports, infer unrestricted schema metadata, or execute arbitrary chart definitions.

Direct chart resources are private to their creator and administrators. Active analysts must have effective dataset `view_rows` both when creating or replacing a definition and whenever querying it; revocation therefore blocks the next direct query. Saved field UUIDs are resolved only against the active published contract, filter values remain bound parameters, and execution uses bounded DuckDB reads over the active Parquet snapshot. Direct chart routes do not give viewers a path to aggregate arbitrary fields.

Dashboard resources are private by default. Owners and administrators may add or revoke view-only grants for an active user or group; current group membership is evaluated on every request. Recipients may read the layout and execute saved items but cannot edit either the layout or grants. Each write is CSRF-protected, complete replacement requires the current opaque revision ETag, and every referenced chart must exist and belong to the dashboard owner. This owner check still applies when an administrator edits the layout, preventing a dashboard from becoming an indirect bridge between private chart owners. The stored layout contains identifiers and geometry only, never chart results or business rows; successful material writes create redacted audit events.

Dashboard-item queries accept only a dashboard ID and stable item ID; the backend resolves the chart from the current saved layout, verifies its owner, and executes its immutable saved aggregate definition. Owners and administrators retain ordinary direct-chart/dataset authorization, while a shared recipient uses the narrow dashboard context. Separate item requests isolate safe failures and prevent one unavailable dataset from suppressing successful sibling charts. The shared context intentionally does not confer dataset `view_rows`, export, source-catalog, or direct-chart access.

## PostgreSQL source safety

- Administrators should configure a dedicated read-only PostgreSQL role.
- Source operations use read-only transactions, statement timeouts, cancellation, and bounded fetch sizes. Preview and refresh obtain their current installation-wide runtime budgets from metadata PostgreSQL immediately before execution.
- Refresh cancellation requires the same owner/administrator authorization and CSRF protection as refresh creation. Durable cancellation is checked again before publication; Celery revoke is not an authorization or correctness boundary.
- Visual queries are generated only from validated structured definitions.
- SQL values are parameterized; source identifiers are resolved from inspected and authorized metadata.
- Advanced SQL accepts one bounded PostgreSQL-dialect query expression with optional read-only CTEs. AST inspection rejects multiple statements, DDL/DML, data-modifying CTEs, `SELECT INTO`, row locks, external parameters, unqualified base relations, and cross-database names.
- SQL definitions preserve original editor text but derive canonical execution SQL and schema-qualified source relations on every load. Every relation is scope-authorized at create/replace, validation, preview, refresh request, and worker execution.
- SQL validation uses `EXPLAIN (FORMAT JSON)` without `ANALYZE` plus a zero-row wrapper for result metadata. Preview and refresh rebuild and wrap execution SQL from the definition; they never trust a stored validation artifact as executable authority.
- SQL parsing is defense in depth, not the primary security boundary. Database privileges and read-only transactions remain mandatory. The dedicated source role should have `EXECUTE` only on trusted functions because functions and views can hide behavior that static relation lineage cannot fully describe.
- Connection scopes narrow but never expand the configured PostgreSQL role. They are enforced during discovery, dataset create/replace, validation, preview, refresh request, and refresh worker execution.
- Analyst catalog collections are filtered before pagination. Inaccessible connections, schemas, and relations are concealed with the same not-found boundaries used for absent resources.
- Catalog discovery requires effective schema `USAGE` and relation `SELECT`; column metadata requires column-level `SELECT`. Foreign-key targets are omitted unless their schema and relation are also visible.
- Catalog statements are static, names and cursors are bound as values, system schemas are hidden by default, and raw source failures are redacted.
- Visual draft compilation composes every schema, relation, alias, selected column, join column, and output name with Psycopg identifiers; it never interprets definition text as SQL. Join types come from the closed `inner`/`left` enum and conditions are equality-only.
- Version-2 definitions authorize the complete relation set at create/replace, validation, preview, refresh request, and worker execution. Partial access cannot execute a joined definition.
- Join suggestions inspect at most ten explicitly requested authorized relations, return structural foreign-key metadata only, and never mutate a draft automatically.
- Validation runs plain `EXPLAIN (FORMAT JSON)` inside the bounded read-only source transaction. It never enables `ANALYZE`, returns source rows, or stores the raw plan.
- Stored validation artifacts contain generated SQL and structural metadata but no credentials, values, or rows. Audit events contain only revision, outcome, counts, and stable finding codes.
- Draft preview never executes stored SQL text: it recompiles the structured definition, binds the row limit separately, and runs inside the bounded read-only source transaction.
- Preview uses a server-side cursor. Managed policy may narrow, but never expand, hard ceilings of 200 rows and one MiB of encoded rows. One individual source value can still be transferred before its encoded size is known; source permissions and the managed statement timeout remain required defenses.
- Refresh checks its managed result-row budget before writing each streamed batch. Exceeding it is a permanent safe failure: the staging candidate is removed, no object or snapshot metadata is published, and any previous active snapshot remains readable.
- Preview audit events contain revision, requested/returned counts, truncation reason, and a stable failure code only. SQL and business values are excluded.

## Connection secrets

- Passwords and CA material are encrypted at rest with AES-256-GCM using an installation key supplied outside the metadata database.
- Ciphertext is bound to its connection UUID and field name; production refuses the documented development key.
- Secret values are write-only through the API.
- Connection test errors are redacted before reaching users or logs.
- `verify-full` is recommended for production; custom CA material exists as a restricted temporary file only while libpq establishes a connection.
- Key storage, backup, and rotation procedures must be documented before production readiness.

## Snapshot and query safety

- Snapshots and exports are private storage objects.
- Object keys are generated, not derived directly from user input.
- Publication is atomic at the metadata reference level.
- S3 publication uploads a completed local candidate, records its SHA-256 in private object metadata, and verifies remote size and checksum metadata with `HEAD` before metadata success can be published.
- S3 reads download to a unique ephemeral path, verify the persisted size and SHA-256 before use, and remove the temporary file after the bounded operation.
- DuckDB queries use validated schema identifiers and bound filter values.
- Query time, result rows, bytes, memory, and concurrency are limited.
- Snapshot access is authorized before storage is touched.
- Dataset freshness requires the same effective `view_rows` permission as snapshot rows, conceals denied resources as not found, and returns only bounded metadata plus stable safe refresh error codes.
- Snapshot row requests resolve field UUIDs against the published schema and never accept SQL expressions. Filter values are normalized by type and parameter-bound.
- Pagination cursors are encrypted and authenticated, contain no readable business values, and are bound to one dataset, snapshot, projection, filter set, and sort order.
- Local snapshot paths are generated metadata keys, resolved under the configured storage root, and rejected if they escape it.
- S3 bucket names, endpoints, and credentials are deployment configuration rather than object identity. Persisted keys remain relative and cannot select another bucket or endpoint.
- Cleanup lists and deletes only generated `datasets/` and `exports/` prefixes. Active metadata references are authoritative; orphan deletion additionally requires a long age grace to avoid racing publication.
- Query audit events contain counts and safe identifiers only; rows, filters, cursor text, SQL, and absolute paths are excluded.

## Export safety

- Export generation requires effective `view_rows` and `export` permissions.
- Export history, job reads, and download-link issuance require effective `export`; non-administrators may access only export jobs they requested.
- DatePipette download capabilities and generated S3 presigned URLs expire after five minutes; files expire after 24 hours.
- Storage buckets disallow public access.
- CSV generation must mitigate formula injection for values beginning with spreadsheet control characters.
- XLSX generation must preserve types and must not embed executable macros.
- Implemented exports are bound to the immutable snapshot selected at creation; Celery receives only the export UUID and duplicate delivery cannot rerun a terminal job.
- CSV and XLSX string cells beginning with spreadsheet formula/control prefixes are escaped with a leading apostrophe. Precision-sensitive decimal values remain text.
- Download capabilities are issued only after dataset and job-ownership authorization, AES-GCM encrypted/authenticated, expire in at most five minutes, and are never stored in metadata or audit events. A capability already issued before grant revocation remains valid until this short expiry. Logical file access ends 24 hours after successful generation even if physical cleanup has not yet removed the private object.
- S3 download resolution verifies the private object before issuing a `307` redirect. The redirect is a bearer capability and is never treated as proof of authorization after its expiry.
- Physical export deletion waits until logical expiry plus a delivery grace. Snapshot deletion waits for supersession grace and the absence of queued, running, or unexpired export references.

## Audit

Audit events are stored separately from application logs in an append-only application table. The application exposes no mutation API for existing events. Events redact secrets and business payloads. This is not proof against a metadata-database administrator; external WORM or SIEM export is a future hardening option.

Only active administrator browser sessions may query the installation-wide event history through `GET /api/v1/audit-events`; bearer service tokens and other roles are rejected. The API bounds each page, uses an opaque newest-first cursor, and permits only typed exact metadata filters plus a timezone-aware occurrence window. It returns producer-controlled redacted details and never expands them from credentials, request bodies, business rows, raw exceptions, or source SQL parameters. Audit reads are intentionally not audited because doing so would recursively create more events; application request IDs still correlate the read in operational logs.

Query Safety planner thresholds and source runtime budgets are installation-wide metadata and may be read or replaced only by an active administrator browser session; replacement is CSRF- and ETag-protected and audited. A policy block remains the default execution boundary. An override requires a fresh validation, an administrator, and a 10–500 character governance reason. The artifact retains policy revision and override provenance, blocked findings become warnings, and a separate audit event records the safe reason and stable finding codes. Operators must not put credentials, SQL values, or business data in an override reason. Overrides do not bypass preview or refresh runtime budgets.

## Network and deployment

- Production endpoints require TLS at the ingress or application boundary.
- Metadata PostgreSQL, Redis, and object storage must not be publicly reachable.
- CORS defaults to the official frontend origin and is explicitly configured for integrations.
- Debug mode and interactive API documentation require deliberate production configuration.
- Containers run as non-root where supported and receive only required filesystem access.

## Security verification

Security-sensitive changes require tests for unauthorized access, cross-resource access, token expiry/revocation, scope narrowing, dashboard indirect access, SQL restrictions, secret redaction, export ownership, and stale session behavior.

## Deferred hardening

- Corporate OIDC/SAML
- External immutable audit sink
- PostgreSQL client certificates and cloud IAM
- Per-installation key rotation workflow
- Fine-grained rate policy by endpoint and integration tier
- Automated security scanning policy beyond baseline dependency and static checks
