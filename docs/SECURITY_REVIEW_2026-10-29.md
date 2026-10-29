# Security review — 2026-10-29

Release-candidate review of the API, worker, official frontend, and single-server deployment at `main` after ADR-0052 and ADR-0053. Lasting rules live in [SECURITY.md](SECURITY.md); this file records what was checked, what was found, and what was accepted.

## Method

- Dependency audit: `pip-audit` over the locked backend runtime dependencies and `npm audit` for the frontend and TypeScript client.
- Route inventory: every public route mapped to its authentication dependency, CSRF requirement, and administrator requirement.
- Black-box authorization probe on a running stack: a second Analyst and a Viewer called 31 read and write routes against another owner's datasets, drafts, exports, refresh runs, charts, dashboards, connections, catalog, and administration resources, and listed every collection.
- Code review of sessions, CSRF, login throttling, password hashing, API tokens, advanced SQL inspection, source transactions, export download capabilities, error handling, CORS, frontend HTML sinks, and the production gateway.
- Deployment check on the production overlay: TLS, redirects, HSTS, closed internal ports, disabled API docs, and per-client sign-in throttling.

## Findings fixed

| Severity | Finding | Fix |
| --- | --- | --- |
| High | Advanced SQL accepted PostgreSQL functions that run SQL given as text, such as `query_to_xml('SELECT * FROM other_schema.table', …)`. Lineage inspection saw only the outer relations, so an Analyst limited by connection scopes could read any relation the source role can read. `dblink`, server file readers, `pg_sleep`, advisory locks, and backend control functions were accepted too. | AST inspection rejects these function families; regression tests cover each family and ordinary functions. |
| High | Behind the Nginx gateway every request came from the gateway address, so five failed sign-ins by anyone throttled sign-in for every user for 15 minutes (fixed in ADR-0053 / PR #77). | Nginx restores the client address and Uvicorn trusts forwarded headers only from private networks. |
| Medium | An export download link issued before a grant was revoked, the account was disabled, or the role was lowered kept working for up to five minutes. | Redeeming a link reloads the issuing person and re-checks status, job ownership, and `export` permission. |
| Medium | `dnspython` 2.8.0 (CVE-2026-114605), pulled in by email validation. | Upgraded to 2.9.0. |
| Low | `undici` advisories in frontend test tooling (not shipped in the built application). | Updated the lockfile with `npm audit fix`. |
| Low | SECURITY.md described a CORS policy that does not exist. | Documentation now states that the API is same-origin only. |

## Confirmed controls

- All 62 cross-owner probes were refused with not-found, forbidden, or validation responses, and other users' collections were empty.
- Every state-changing route requires a session-bound CSRF token, except sign-in. The remaining POST routes that only read (`rows:query`, chart queries and previews, dashboard item queries, join suggestions) rely on `SameSite=Strict` session cookies. Bearer tokens are never sent automatically by browsers.
- Session and CSRF tokens are stored only as SHA-256 hashes and compared in constant time. Sessions end immediately when an account is disabled. Passwords use Argon2id, and the last active administrator cannot be removed.
- Advanced SQL is reparsed to exactly one query, re-rendered from the AST, and executed inside a `READ ONLY` transaction with a statement timeout. Psycopg's extended protocol rejects stacked statements.
- Unhandled errors return a generic response without stack traces. Debug mode is off, and production disables `/docs` and `/openapi.json`.
- The frontend has no `innerHTML` sinks. Chart labels are drawn on canvas, and the Content Security Policy forbids inline scripts.
- The production gateway serves a valid certificate, redirects HTTP with `308`, sends HSTS, and publishes only ports 80 and 443.

## Accepted risks and follow-ups

- **Source-role privileges remain the primary SQL boundary.** The function denylist cannot know user-defined functions or views that read other schemas. Administrators should use a dedicated role with `SELECT` only on intended relations and without `EXECUTE` on untrusted functions.
- **Download capabilities travel in the URL** and may appear in gateway access logs for their five-minute lifetime.
- **Forwarded addresses from private networks are trusted.** A client that reaches Nginx directly from a private network can choose its address and weaken only the per-client sign-in limit.
- **No general request rate limit** beyond sign-in. Authenticated load is bounded by the interactive and background admission limits.
- **Aggregates have no minimum group size.** Dashboard filters let readers narrow results to small groups of mapped values (ADR-0032, ADR-0052).
- **Host hardening is outside the product.** This covers SSH configuration, operating-system updates, and firewalling beyond the published ports.
