# Contributing to DataPipette

Thank you for your interest in DataPipette. The project is a functional pre-alpha: the evaluation stack runs end to end, but there is no production release yet. Focused changes aligned with the approved MVP are preferred over broad feature additions.

## Before proposing a change

1. Read the [product vision](docs/PRODUCT.md) and [MVP scope](docs/MVP.md).
2. Check [open questions](docs/OPEN_QUESTIONS.md) and relevant [ADRs](docs/adr/README.md).
3. Follow the [Git workflow](docs/GIT_WORKFLOW.md) and prepare a local execution plan for non-trivial work.
4. For a feature or architecture change, open a discussion or issue before implementing a large patch.
5. Never include real customer data, credentials, snapshots, or export artifacts.

## Complete evaluation stack

The self-contained evaluation topology requires only Git, Docker, and Docker Compose v2 on the host. Configure the ignored environment file, including unique infrastructure, encryption, and bootstrap-administrator secrets, then start the entire application:

```bash
cp deploy/compose/.env.example .env
chmod 600 .env
# Edit .env before continuing.
docker compose --env-file .env -f deploy/compose/compose.yaml up -d --build --wait
```

Open `http://127.0.0.1:8080`. The command builds the API and frontend, applies migrations, initializes storage, creates the first administrator if needed, and starts PostgreSQL, Redis, MinIO, API, worker, scheduler, and the same-origin frontend gateway. Repeating it never resets an existing administrator. See [the evaluation deployment guide](docs/DEPLOYMENT.md) for server access and operational constraints.

## Host-based development setup

Requirements:

- uv with Python 3.12 support;
- Node.js 22.12 or newer with npm for the generated TypeScript client;
- Docker with Compose v2 for PostgreSQL, Redis, and the MinIO-compatible development object store.

Install the locked workspace, start only local infrastructure, initialize its private bucket, and apply metadata migrations:

```bash
uv sync --locked --all-packages --all-groups
cp deploy/compose/.env.example .env
docker compose --env-file .env -f deploy/compose/compose.yaml up -d --wait metadata-db redis minio
uv run --package datapipette datapipette-storage-init
uv run --package datapipette alembic -c backend/alembic.ini upgrade head
```

The storage initialization command creates the configured private bucket idempotently. The example credentials and HTTP endpoint are development-only.

Create the first local administrator. The command prompts for the password twice and refuses to create another bootstrap administrator after the first account exists:

```bash
uv run --package datapipette datapipette-admin bootstrap-admin \
  --email admin@example.com \
  --display-name "Local administrator"
```

Start the development API:

```bash
uv run --package datapipette datapipette
```

Build the generated API client, install the official frontend, and start its development server in another terminal:

```bash
npm ci --prefix clients/typescript
npm run build --prefix clients/typescript
npm ci --prefix frontend
npm run dev --prefix frontend
```

Open `http://127.0.0.1:5173`. The development server proxies same-origin `/api` requests to the API on port 8000 so secure browser-session behavior is exercised without enabling permissive CORS.

Run a single-concurrency Celery worker on the `refresh` and `export` queues in another terminal:

```bash
uv run --package datapipette celery \
  --app datapipette.infrastructure.celery_app:celery_app \
  worker --queues refresh,export --concurrency 1 --loglevel INFO
```

Run the durable refresh scheduler and analytical-object cleanup loop in another terminal:

```bash
uv run --package datapipette datapipette-scheduler
```

`DATAPIPETTE_INSTALLATION_TIMEZONE` selects the single IANA timezone used for daily schedules. `DATAPIPETTE_SCHEDULER_POLL_SECONDS` and `DATAPIPETTE_SCHEDULER_BATCH_SIZE` bound polling frequency and work per tick. Metadata PostgreSQL owns schedule state; stopping the scheduler delays work without losing the next due occurrence.

Cleanup is enabled by default. `DATAPIPETTE_CLEANUP_INTERVAL_SECONDS` and `DATAPIPETTE_CLEANUP_BATCH_SIZE` bound each tick. `DATAPIPETTE_CLEANUP_SNAPSHOT_GRACE_SECONDS` delays superseded-snapshot removal, `DATAPIPETTE_CLEANUP_EXPORT_GRACE_SECONDS` protects delivery after logical expiry, and `DATAPIPETTE_CLEANUP_ORPHAN_GRACE_SECONDS` protects the upload-to-metadata publication window. Disable physical deletion with `DATAPIPETTE_CLEANUP_ENABLED=false`; logical export expiry remains enforced.

Refresh state is durable in metadata PostgreSQL and Redis is only the transport. A directly started process uses local `.local/snapshots` storage by default; Compose selects the shared private S3-compatible bucket. Never commit, share, or publicly serve snapshots because they contain business data.

Dataset validation includes a non-executing Query Safety Advisor. `DATAPIPETTE_QUERY_SAFETY_ESTIMATED_ROWS_WARNING/BLOCK`, `TOTAL_COST_WARNING/BLOCK`, and `JOIN_COUNT_WARNING/BLOCK` configure installation-level policy; each warning threshold must stay below its block threshold. `SEQUENTIAL_SCAN_ROWS_WARNING`, `SORT_ROWS_WARNING`, and `STALE_STATISTICS_DAYS` configure advisory findings. Typed recommendations map findings to review actions and can identify visual join columns as index candidates, but they never contain or apply DDL. These values are PostgreSQL planner signals rather than runtime guarantees, and the advisor never runs `EXPLAIN ANALYZE` or source DDL. Completed refresh runs expose the captured planner estimate, actual materialized rows, execution duration, and a safe estimate ratio without an extra source query.

Interactive snapshot reads use embedded DuckDB against the active Parquet object. S3-backed objects are downloaded to a unique temporary file, checked against their recorded size and SHA-256, queried, and then removed. `DATAPIPETTE_SNAPSHOT_QUERY_TIMEOUT_SECONDS`, `DATAPIPETTE_SNAPSHOT_QUERY_MEMORY_LIMIT_MB`, `DATAPIPETTE_SNAPSHOT_QUERY_THREADS`, `DATAPIPETTE_SNAPSHOT_QUERY_CONCURRENCY`, `DATAPIPETTE_SNAPSHOT_QUERY_TEMP_LIMIT_MB`, and `DATAPIPETTE_SNAPSHOT_QUERY_MAX_RESPONSE_BYTES` seed revision-zero policy defaults. After the first administrator replacement, metadata PostgreSQL is authoritative for new table, chart, and dashboard-item requests. Dedicated non-pooled metadata PostgreSQL sessions hold advisory-lock slots and enforce the captured concurrency across API processes and hosts without consuming the ordinary metadata pool; these connections must not pass through transaction pooling. DuckDB provides parameterized projection, predicates, ordering, and pagination; `pytz` supports its timezone-aware Python timestamp conversion.

Background CSV/XLSX exports use the same configured private storage backend and a separate Celery queue. `DATAPIPETTE_EXPORT_TIMEOUT_SECONDS`, `DATAPIPETTE_EXPORT_MEMORY_LIMIT_MB`, `DATAPIPETTE_EXPORT_THREADS`, `DATAPIPETTE_EXPORT_TEMP_LIMIT_MB`, `DATAPIPETTE_EXPORT_MAX_ROWS`, and `DATAPIPETTE_EXPORT_MAX_BYTES` seed revision-zero Query Safety defaults; `DATAPIPETTE_BACKGROUND_JOB_CONCURRENCY` seeds the shared refresh/export admission limit. `DATAPIPETTE_EXPORT_FETCH_ROWS` remains deployment-level batching. Metadata PostgreSQL session advisory locks enforce concurrency across workers and hosts, so worker database connections must be direct or session-pooled rather than transaction-pooled. Files are logically inaccessible 24 hours after success and are physically removed after the configured cleanup grace while job history remains. S3-backed downloads keep the DataPipette five-minute capability check and then redirect to an equally short-lived presigned private-object URL.

`DATAPIPETTE_SNAPSHOT_CACHE_MAX_MB` bounds the verified local cache of S3 snapshot objects used by interactive queries and exports (default 4096, `0` disables it), and `DATAPIPETTE_SNAPSHOT_CACHE_PATH` sets its directory (default `<snapshot storage path>/.object-cache`). `DATAPIPETTE_OBJECT_STORAGE_BACKEND` selects `local` or `s3`. S3 additionally requires `DATAPIPETTE_S3_BUCKET`, `DATAPIPETTE_S3_ACCESS_KEY_ID`, and `DATAPIPETTE_S3_SECRET_ACCESS_KEY`; compatible installations can set `DATAPIPETTE_S3_ENDPOINT_URL`, region, and path/virtual addressing. Production requires the S3 backend and rejects non-HTTPS custom endpoints. Optional `DATAPIPETTE_S3_SERVER_SIDE_ENCRYPTION=AES256` asks the provider for server-side encryption.

The API listens on `http://127.0.0.1:8000` by default. Useful endpoints are:

- `GET /api/v1/health/live` for process liveness;
- `GET /api/v1/health/ready` for PostgreSQL and Redis readiness;
- `POST /api/v1/auth/sessions` for browser sign-in;
- `GET /api/v1/auth/me` for the current local user;
- `DELETE /api/v1/auth/sessions/current` for sign-out;
- `GET /api/v1/users` for administrator-only local account management;
- `GET /api/v1/connections` for administrator-only PostgreSQL source management;
- `POST /api/v1/datasets/{dataset_id}/exports` for administrator-only background CSV/XLSX generation;
- `/docs` and `/openapi.json` for the development API contract.

Browser authentication uses an HTTP-only session cookie. State-changing requests also send the readable `datapipette_csrf` cookie value in the `X-CSRF-Token` header. Production forces secure cookies even if local configuration attempts to disable them.

The independently buildable TypeScript client is generated from the committed OpenAPI contract. Install its exact dependencies and verify it from the repository root:

```bash
npm ci --prefix clients/typescript
npm run generate:check --prefix clients/typescript
npm run typecheck --prefix clients/typescript
npm test --prefix clients/typescript
npm run build --prefix clients/typescript
npm ci --prefix frontend
npm run typecheck --prefix frontend
npm test --prefix frontend
npm run build --prefix frontend
npm run budget --prefix frontend
```

After an intentional API change, run `npm run generate --prefix clients/typescript` and commit the reviewed generated schema together with `docs/openapi.json`. See the [client README](clients/typescript/README.md) for browser and service-token usage and the executable dashboard example.

The bootstrap administrator creates further local accounts through `POST /api/v1/users`. User creation and mutations are administrator-only and CSRF-protected. DataPipette prevents supported API operations from disabling or demoting the final active administrator.

PostgreSQL connection passwords and optional CA certificates require `DATAPIPETTE_CONNECTION_ENCRYPTION_KEY`, a URL-safe base64 encoding of exactly 32 random bytes. The built-in default is only for local development and is rejected in production. Generate a key without storing it in shell history:

```bash
uv run --package datapipette python -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("="))'
```

Back up this key separately from metadata PostgreSQL. Losing it makes stored source credentials unrecoverable.

Back up, verify, and restore a running Compose installation with `deploy/compose/backup.sh`; the script runs `datapipette-backup` through the backend image. See [the deployment guide](docs/DEPLOYMENT.md#backup-and-restore) for scheduling, key handling, and restore steps.

Stop local services without deleting their volumes:

```bash
docker compose --env-file .env -f deploy/compose/compose.yaml stop
```

Compose defaults are development-only. Override `DATAPIPETTE_POSTGRES_PASSWORD`, MinIO credentials, and related variables through an explicit Compose environment file for any shared environment. Application settings use the `DATAPIPETTE_` prefix; important values include `ENVIRONMENT`, `LOG_LEVEL`, `HOST`, `PORT`, `DOCS_ENABLED`, `METADATA_DATABASE_URL`, `REDIS_URL`, `READINESS_TIMEOUT_SECONDS`, `SESSION_TTL_SECONDS`, `SESSION_COOKIE_SECURE`, `LOGIN_RATE_LIMIT_ATTEMPTS`, `LOGIN_RATE_LIMIT_WINDOW_SECONDS`, `CONNECTION_ENCRYPTION_KEY`, `SOURCE_CONNECT_TIMEOUT_SECONDS`, `SOURCE_STATEMENT_TIMEOUT_SECONDS`, `PREVIEW_STATEMENT_TIMEOUT_SECONDS`, `PREVIEW_MAX_ROWS`, `PREVIEW_MAX_RESPONSE_BYTES`, `REFRESH_STATEMENT_TIMEOUT_SECONDS`, `REFRESH_MAX_ROWS`, `REFRESH_BATCH_ROWS`, `REFRESH_MAX_ATTEMPTS`, `REFRESH_RETRY_BASE_SECONDS`, `REFRESH_RETRY_MAX_SECONDS`, `REFRESH_CANCEL_POLL_SECONDS`, `REFRESH_RUN_LEASE_SECONDS`, `INSTALLATION_TIMEZONE`, `SCHEDULER_POLL_SECONDS`, `SCHEDULER_BATCH_SIZE`, `OBJECT_STORAGE_BACKEND`, `SNAPSHOT_STORAGE_PATH`, `SNAPSHOT_CACHE_PATH`, `SNAPSHOT_CACHE_MAX_MB`, the `QUERY_SAFETY_*`, `S3_*`, `CLEANUP_*`, `SNAPSHOT_QUERY_*`, `EXPORT_*`, and `BACKGROUND_JOB_CONCURRENCY` settings documented above. Source, interactive-query, export, and background-concurrency limit settings seed revision zero; after the first administrator replacement, the durable metadata policy is authoritative for new governed operations.

API documentation is enabled by default only in the `development` environment. Connection URLs are represented as secrets and must never be printed or committed.

## Verification

Integration tests reset metadata tables with `TRUNCATE` before each test. Point them at a dedicated database, never at the `datapipette` database of a Compose stack you use; for example, create `datapipette_test` in the local metadata container, migrate it, and export both `DATAPIPETTE_METADATA_DATABASE_URL` and `DATAPIPETTE_TEST_DATABASE_URL` with that database name before running `pytest`.

Run the same essential checks as CI from the repository root:

```bash
uv lock --check
uv run --package datapipette ruff format --check backend
uv run --package datapipette ruff check backend
uv run --package datapipette mypy
uv run python -m unittest discover -s benchmarks/tests
shellcheck deploy/compose/backup.sh deploy/compose/new-env.sh deploy/compose/healthcheck.sh
uv run --package datapipette alembic -c backend/alembic.ini upgrade head
uv run --package datapipette alembic -c backend/alembic.ini check
uv run --package datapipette pytest
uv run --package datapipette alembic -c backend/alembic.ini heads
uv run --package datapipette python tools/export_openapi.py
git diff --exit-code -- docs/openapi.json
docker compose --env-file .env -f deploy/compose/compose.yaml config --quiet
deploy/compose/new-env.sh --domain bi.example.com --admin-email admin@example.com --output /tmp/datapipette-production.env
docker compose --env-file /tmp/datapipette-production.env -f deploy/compose/compose.yaml -f deploy/compose/compose.production.yaml config --quiet
rm /tmp/datapipette-production.env
npm ci --prefix clients/typescript
npm run generate:check --prefix clients/typescript
npm run typecheck --prefix clients/typescript
npm test --prefix clients/typescript
npm run build --prefix clients/typescript
npm pack --dry-run ./clients/typescript
npm ci --prefix frontend
npm run typecheck --prefix frontend
npm test --prefix frontend
npm run build --prefix frontend
npm run budget --prefix frontend
docker compose -f deploy/compose/compose.yaml build frontend
```

The migration commands require the local metadata PostgreSQL service from the host-based setup. To include the real MinIO round-trip tests, export the `DATAPIPETTE_TEST_S3_ENDPOINT`, `DATAPIPETTE_TEST_S3_BUCKET`, `DATAPIPETTE_TEST_S3_ACCESS_KEY_ID`, and `DATAPIPETTE_TEST_S3_SECRET_ACCESS_KEY` values for the running development service before invoking `pytest`.

`pytest` enforces at least 90% backend coverage. Keep the lockfile synchronized whenever project dependencies change.

## Change expectations

- Keep pull requests focused on one coherent outcome.
- Add tests that fail before and pass after a behavioral change.
- Regenerate and commit `docs/openapi.json` whenever the public API changes.
- Include database migrations for metadata schema changes.
- Explain security, compatibility, and operational consequences.
- Update affected product, architecture, API, or security documentation.
- Do not silently expand MVP scope.

## Architecture decisions

A change that alters a durable system boundary, persistence model, security model, public contract strategy, or major dependency requires an ADR. Use the structure in [docs/adr/README.md](docs/adr/README.md).

## Reporting security issues

Do not open a public issue for a suspected vulnerability. Follow [SECURITY.md](SECURITY.md).

## License of contributions

By intentionally submitting a contribution for inclusion in DataPipette, you agree that it may be distributed under the Apache License 2.0, consistent with the repository license.
