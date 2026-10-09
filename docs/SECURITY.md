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
- Token plaintext is shown once and only a secure hash and non-secret identifier are retained.
- Tokens may be expiring or non-expiring, are revocable, and support overlapping rotation.
- Token scopes may only narrow service-account permissions.
- Logs and traces must redact authorization headers and token-like values.

## Authorization

Authorization combines:

1. principal status;
2. system role;
3. token scopes when applicable;
4. direct and group ACL grants;
5. resource state; and
6. request context.

New resources are private to their owner and administrators. Grants are additive in MVP. Direct dataset access is distinct from dashboard-context access to aggregated chart results.

A viewer with dashboard access may receive chart results but must not use that route to browse dataset rows, create exports, infer unrestricted schema metadata, or execute arbitrary chart definitions.

## PostgreSQL source safety

- Administrators should configure a dedicated read-only PostgreSQL role.
- Source operations use read-only transactions, statement timeouts, cancellation, and bounded fetch sizes.
- Visual queries are generated only from validated structured definitions.
- SQL values are parameterized; source identifiers are resolved from inspected and authorized metadata.
- Advanced SQL accepts one `SELECT` or `WITH` statement and rejects multiple statements, DDL, and DML.
- SQL parsing is defense in depth, not the primary security boundary. Database privileges and read-only transactions remain mandatory.
- Once connection grants are implemented, schema/table allowlists will be enforced during discovery, validation, preview, and refresh.
- Until connection grants and allowlists are implemented, live catalog endpoints are administrator-only and expose no more than the configured PostgreSQL role can access.
- Catalog discovery requires effective schema `USAGE` and relation `SELECT`; column metadata requires column-level `SELECT`. Foreign-key targets are omitted unless their schema and relation are also visible.
- Catalog statements are static, names and cursors are bound as values, system schemas are hidden by default, and raw source failures are redacted.

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
- DuckDB queries use validated schema identifiers and bound filter values.
- Query time, result rows, bytes, memory, and concurrency are limited.
- Snapshot access is authorized before storage is touched.

## Export safety

- Export generation requires direct dataset export permission.
- Export ownership and administrator access are checked before issuing a signed URL.
- Signed URLs expire after five minutes; files expire after 24 hours.
- Storage buckets disallow public access.
- CSV generation must mitigate formula injection for values beginning with spreadsheet control characters.
- XLSX generation must preserve types and must not embed executable macros.

## Audit

Audit events are stored separately from application logs in an append-only application table. The application exposes no mutation API for existing events. Events redact secrets and business payloads. This is not proof against a metadata-database administrator; external WORM or SIEM export is a future hardening option.

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
