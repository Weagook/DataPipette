# ADR-0007: Installation-key encryption for PostgreSQL connections

- Status: Accepted
- Date: 2026-10-09

## Context

DatePipette must store PostgreSQL passwords and optional private CA material while keeping the API stateless and horizontally scalable. Metadata database backups must not be sufficient to recover source credentials. Connection tests must support PostgreSQL TLS modes without exposing driver errors or constructing password-bearing URLs.

## Decision

- Passwords and custom CA certificates are encrypted with AES-256-GCM before entering metadata persistence.
- The installation supplies one URL-safe base64 32-byte key outside metadata PostgreSQL. Production rejects the documented development key.
- Every encryption uses a fresh 96-bit nonce. Associated authenticated data binds the ciphertext version, connection UUID, and field name.
- Stored envelopes are versioned so a future key/envelope migration can coexist with existing rows.
- Secret API fields are write-only. Responses expose only whether a password or CA certificate exists.
- Psycopg receives connection values as separate keyword parameters, never as a password-bearing DSN.
- Supported SSL modes are `disable`, `require`, `verify-ca`, and `verify-full`. Verification modes require explicit CA material in this slice; `verify-full` is recommended for production.
- A custom CA is decrypted only for the operation and written to a permission-restricted temporary file required by libpq, then removed.
- Public test failures use stable redacted codes. Raw driver messages are not persisted, logged, or returned.

## Consequences

- Stealing metadata PostgreSQL without the installation key does not reveal source passwords or CA material.
- Losing the installation key makes stored connection secrets unrecoverable; backup and restore procedures must include it separately.
- Rotating the key requires a future explicit re-encryption workflow.
- Application processes that test or use sources require the installation key, while PostgreSQL and Redis do not.
- `require` protects transport from passive observation but does not authenticate the server; production operators should prefer `verify-full`.

## Alternatives considered

- Database-side encryption was rejected because a database compromise or backup containing the decryption key would collapse the security boundary.
- Fernet was rejected in favor of an explicit AES-GCM envelope whose AAD binds each secret to its owning connection and field.
- Plaintext CA storage was rejected because private trust configuration is part of the connection secret boundary.
- Returning raw libpq errors was rejected because they may contain host, database, username, certificate-path, or operational details.
