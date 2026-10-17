# ADR-0043: Convergent one-command evaluation deployment

- Status: Accepted
- Date: 2026-10-17

## Context

The repository's original Compose topology supplied PostgreSQL, Redis, MinIO, a worker, and a scheduler while requiring Python, uv, Node.js, migrations, administrator bootstrap, the API, and Vite to run directly on the host. That split is useful while developing but makes portfolio review and single-server evaluation unnecessarily error-prone. Browser sessions also benefit from one same-origin entry point rather than a separately configured CORS boundary.

The first administrator requires a secret, yet a restart must not reset credentials or fail merely because bootstrap already completed. Putting the password in a command argument would expose it through process inspection and logs.

## Decision

- Build the backend and frontend as separate images from the monorepo. API, migration, bootstrap, worker, scheduler, and storage initialization reuse one non-root backend image.
- Serve the static React build and proxy `/api` through one Nginx gateway.
- Make migrations, private-bucket initialization, and first-administrator bootstrap dependency-gated one-shot services. Long-running application services start only after their prerequisites succeed.
- Source the initial administrator email, display name, and password from the operator's ignored `.env`. Compose mounts that file read-only only into the one-shot bootstrap container; the bootstrap CLI extracts the password from the dotenv secret without logging it. Direct administration may instead use a named environment variable or dedicated secret file.
- Add an explicit `--if-needed` bootstrap mode. It exits successfully when an administrator already exists and never updates or compares existing credentials.
- Bind every published port to loopback by default. Operators may deliberately expose the gateway; infrastructure services stay loopback-only.
- Keep the direct host-based development workflow available independently of the complete evaluation topology.

## Consequences

- A clean installation or update converges through one `docker compose up -d --build --wait` command after `.env` is configured.
- The host no longer needs Python, uv, Node.js, PostgreSQL, Redis, or MinIO for evaluation.
- Browser cookies and CSRF remain same-origin through the gateway, and the frontend still consumes only the public API.
- Re-running Compose is safe for the initial identity, but changing bootstrap variables after creation has no effect. Account changes remain explicit administrator operations.
- The environment file and local Compose secret mechanism are appropriate for evaluation, not the final production secret-management boundary.
- Embedded infrastructure, source builds, and missing TLS/backup/observability automation prevent this topology from being described as production-ready.

## Alternatives considered

- A host-run API and Vite frontend were retained for contributor development but rejected as the evaluation default because they require two language toolchains and multiple manual steps.
- Passing the password as a CLI argument was rejected because process listings and orchestration metadata expose arguments.
- Resetting the administrator on every start was rejected because deployment configuration must not silently mutate a human credential.
- Creating an administrator through a public unauthenticated setup endpoint was rejected because it introduces a race-sensitive network bootstrap surface.
