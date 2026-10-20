# ADR-0005: Server-side opaque browser sessions

- Status: Accepted
- Date: 2026-10-08

## Context

DataPipette uses local human accounts in the MVP and must support immediate logout, account deactivation, administrator-controlled session invalidation, CSRF protection, and future SSO without coupling domain authorization to one identity provider. Browser credentials must not be exposed to JavaScript or stored in browser web storage.

## Decision

Human browser authentication uses server-side sessions stored in metadata PostgreSQL.

- Login creates a cryptographically random opaque session token and a separate random CSRF token.
- The browser receives the session token only in an `HttpOnly`, `SameSite=Strict` cookie. Production cookies are `Secure`, have path `/`, and have no `Domain` attribute.
- PostgreSQL stores SHA-256 hashes of session and CSRF tokens, never their plaintext values.
- Sessions have absolute server-side expiry and explicit revocation state.
- State-changing cookie-authenticated requests must provide the readable CSRF cookie value in the `X-CSRF-Token` header. The backend validates the cookie, header, and stored session-bound hash in constant time.
- Login credentials are verified with Argon2id. Unknown accounts still execute a dummy password verification.
- Redis applies short-lived login-attempt limits; PostgreSQL remains authoritative for identities and sessions.
- Authentication and session lifecycle actions create redacted append-only audit events.

## Consequences

- Logout and account deactivation take effect without waiting for a signed token to expire.
- Each authenticated request requires one indexed session lookup in PostgreSQL; horizontal API instances share that state.
- Database theft does not directly expose replayable session cookies, although active sessions must still be revoked after a metadata compromise.
- The official frontend must echo the CSRF cookie value in a custom header for state-changing requests.
- Local HTTP development uses non-`Secure` cookies; production configuration always uses `Secure` cookies behind HTTPS.
- API bearer tokens for service accounts remain a separate future mechanism and do not reuse browser sessions.

## Alternatives considered

- Self-contained JWT browser tokens were rejected because immediate revocation, account-state enforcement, and secure logout would require additional server-side state while adding token-rotation complexity.
- Storing session tokens in browser local storage was rejected because JavaScript and XSS could read them.
- Relying only on `SameSite` was rejected because it is defense in depth rather than the sole CSRF boundary.
- A naive double-submit cookie was rejected in favor of a token bound to authoritative server-side session state.
