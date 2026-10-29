# ADR-0053: Single-server production Compose overlay

- Status: Accepted
- Date: 2026-10-29

## Context

The Compose topology (ADR-0043) was an evaluation installation: plain HTTP on loopback, development credentials as defaults, and a production mode that rejected the embedded MinIO because its endpoint is not HTTPS. Teams that want to use DataPipette with real data on one server had no supported path, and an evaluation installation that started with the development encryption key could not move to production without losing every saved source password.

Behind any reverse proxy the API also saw the proxy's address as every client's address, so the per-client sign-in throttle became one shared bucket: a few failed attempts by anyone blocked sign-in for everybody for the throttle window.

## Decision

- Keep one topology and add `deploy/compose/compose.production.yaml` as an overlay. It adds a pinned Caddy gateway that obtains and renews certificates for `DATAPIPETTE_DOMAIN`, redirects to HTTPS, and sends HSTS; it removes every other host port; it sets production mode and disables API docs; and it uses required-variable interpolation so the stack refuses to start without a domain, database password, object-storage credentials, and encryption key.
- Allow plain HTTP to S3 in production only through the explicit `DATAPIPETTE_S3_PRIVATE_NETWORK_HTTP` setting, which the overlay sets for the embedded MinIO on the private Compose network. Every other production S3 endpoint must still use HTTPS.
- Provide `deploy/compose/new-env.sh` to write a mode-0600 environment file with unique random secrets without printing them.
- Provide `datapipette-backup rotate-key`, wrapped by `deploy/compose/backup.sh rotate-key --yes`. It decrypts every stored connection secret with the current key and re-encrypts it with the new one in a single transaction, changes nothing if any secret fails, then switches the environment file and verifies every secret.
- Let `backup.sh` use the production Compose files through `DATAPIPETTE_COMPOSE_FILES`, so scheduled backups and restores run against the same configuration.
- Trust `X-Forwarded-For` only from private network addresses: Nginx restores the client address from the gateway with its real-IP module and passes it on, and Uvicorn accepts forwarded headers only from those ranges.

## Consequences

- One documented command set takes a server from an empty directory to HTTPS with closed internal ports, and an evaluation installation can be promoted in place without re-entering source passwords.
- Rotating the key invalidates encrypted table cursors and pending download links, and backups made before the rotation need the old key to restore.
- A client inside a private network that connects to Nginx directly, without the gateway, can supply its own `X-Forwarded-For` value and so weaken the per-client part of sign-in throttling. The per-account throttle is unaffected.
- All state still lives on one host and one disk; off-host backup copies, monitoring, and managed services remain operator responsibilities.

## Alternatives considered

- **A separate production Compose file:** rejected because two full topologies would drift. The overlay changes only what production needs.
- **TLS between the application and MinIO:** rejected for the single-host case because traffic never leaves the host's private bridge network, and certificate management for an internal service adds failure modes without protecting against a realistic attacker.
- **Nginx with Certbot instead of Caddy:** rejected because certificate renewal would need an extra scheduled job and reload; Caddy handles issuance and renewal itself.
- **Trusting forwarded headers from any address:** rejected because a direct client could then choose any address and bypass the per-client throttle completely.
