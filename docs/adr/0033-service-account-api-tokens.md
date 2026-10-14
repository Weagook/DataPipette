# ADR-0033: Service accounts and scoped opaque API tokens

## Status

Accepted — 2026-10-14

## Context

External CRM, banking, and embedded applications need a stable non-human identity. Reusing an administrator password or browser cookie would prevent least privilege, safe rotation, independent revocation, and useful audit attribution. A broad bearer credential would also undermine the object ACLs and Viewer ceiling already enforced by the backend.

## Decision

- Model a service account as an explicit non-human account type with its own name, `viewer` or `analyst` role, active/disabled lifecycle, group memberships, and direct ACL grants. Service accounts cannot hold the installation-wide `admin` role and cannot create browser sessions.
- Administrators manage service accounts and tokens through CSRF-protected browser-session APIs. One account may have multiple tokens so rotation can overlap safely.
- Generate 256-bit opaque secrets with a recognizable `dpp_` prefix. Return plaintext exactly once in the create response; metadata PostgreSQL stores only the SHA-256 digest. Token listings never contain plaintext or a recoverable secret.
- Tokens may be non-expiring or carry an explicit UTC expiry. Every bearer request reloads token revocation, expiry, service-account status, role, scopes, and current object/group grants from metadata PostgreSQL. Successful authentication updates `last_used_at`.
- Begin with one closed scope, `dashboards:read`. It permits dashboard collection/read and saved-item aggregate queries only. It does not permit service-account administration, grant management, dashboard mutation, direct chart access, dataset rows/exports, source discovery, refresh, or authoring.
- Token scopes only narrow authorization. A matching scope is insufficient without an active account and effective dashboard grant. Dashboard item resolution, chart ownership, saved-definition validation, active-snapshot selection, query bounds, and safe failures remain unchanged.
- Attribute aggregate query audit events to `service_account`, never store bearer plaintext in audits/logs, and return the same `401` for unknown, expired, revoked, malformed, or disabled-account tokens.

## Consequences

- Integrations can use one convenient token while administrators retain immediate account-wide disable and per-token revocation.
- Multiple tokens allow no-downtime rotation without sharing credentials between external systems.
- Digest storage protects token plaintext from a metadata read, but a bearer token remains a password-equivalent secret and requires TLS plus external secret management.
- `last_used_at` performs one metadata write per successful bearer request in this initial implementation. Bounded/coalesced usage tracking is a future optimization if traffic requires it.
- Additional scopes require a separate authorization review and tests; they must never be inferred from the service account's role alone.

## Alternatives considered

- **JWT access tokens:** rejected because immediate revocation, account disable, scope changes, and last-used tracking already require authoritative server state.
- **Reuse human accounts and passwords:** rejected because it obscures ownership, encourages credential sharing, and prevents token-level rotation/revocation.
- **One installation-wide API key:** rejected because it cannot express per-integration identity, ACLs, scopes, expiry, or audit attribution.
- **Expose every existing endpoint to bearer authentication immediately:** rejected because each operation needs an explicit scope and authorization review.
