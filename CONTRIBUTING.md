# Contributing to DatePipette

Thank you for your interest in DatePipette. The project is pre-alpha and is establishing its first vertical slice. Focused changes aligned with the approved MVP are preferred over broad feature additions.

## Before proposing a change

1. Read the [product vision](docs/PRODUCT.md) and [MVP scope](docs/MVP.md).
2. Check [open questions](docs/OPEN_QUESTIONS.md) and relevant [ADRs](docs/adr/README.md).
3. Follow the [Git workflow](docs/GIT_WORKFLOW.md) and prepare a local execution plan for non-trivial work.
4. For a feature or architecture change, open a discussion or issue before implementing a large patch.
5. Never include real customer data, credentials, snapshots, or export artifacts.

## Development setup

Requirements:

- uv with Python 3.12 support;
- Docker with Compose v2 for PostgreSQL, Redis, and the MinIO-compatible development object store.

Install the locked workspace, start local dependencies, and apply metadata migrations:

```bash
uv sync --locked --all-packages --all-groups
cp deploy/compose/.env.example .env
docker compose --env-file .env -f deploy/compose/compose.yaml up -d --wait
uv run --package datepipette alembic -c backend/alembic.ini upgrade head
```

The one-shot `minio-init` service creates the configured private bucket idempotently before the worker starts. The example credentials and HTTP endpoint are development-only.

Create the first local administrator. The command prompts for the password twice and refuses to create another bootstrap administrator after the first account exists:

```bash
uv run --package datepipette datepipette-admin bootstrap-admin \
  --email admin@example.com \
  --display-name "Local administrator"
```

Start the development API:

```bash
uv run --package datepipette datepipette
```

Compose also starts a single-concurrency Celery worker on the `refresh` and `export` queues. To run a worker directly while developing:

```bash
uv run --package datepipette celery \
  --app datepipette.infrastructure.celery_app:celery_app \
  worker --queues refresh,export --concurrency 1 --loglevel INFO
```

Compose also starts the durable refresh scheduler. To run it directly, use:

```bash
uv run --package datepipette datepipette-scheduler
```

`DATEPIPETTE_INSTALLATION_TIMEZONE` selects the single IANA timezone used for daily schedules. `DATEPIPETTE_SCHEDULER_POLL_SECONDS` and `DATEPIPETTE_SCHEDULER_BATCH_SIZE` bound polling frequency and work per tick. Metadata PostgreSQL owns schedule state; stopping the scheduler delays work without losing the next due occurrence.

Refresh state is durable in metadata PostgreSQL and Redis is only the transport. A directly started process uses local `.local/snapshots` storage by default; Compose selects the shared private S3-compatible bucket. Never commit, share, or publicly serve snapshots because they contain business data.

Interactive snapshot reads use embedded DuckDB against the active Parquet object. S3-backed objects are downloaded to a unique temporary file, checked against their recorded size and SHA-256, queried, and then removed. `DATEPIPETTE_SNAPSHOT_QUERY_TIMEOUT_SECONDS`, `DATEPIPETTE_SNAPSHOT_QUERY_MEMORY_LIMIT_MB`, `DATEPIPETTE_SNAPSHOT_QUERY_THREADS`, `DATEPIPETTE_SNAPSHOT_QUERY_CONCURRENCY`, `DATEPIPETTE_SNAPSHOT_QUERY_TEMP_LIMIT_MB`, and `DATEPIPETTE_SNAPSHOT_QUERY_MAX_RESPONSE_BYTES` bound each query. DuckDB provides parameterized projection, predicates, ordering, and pagination; `pytz` supports its timezone-aware Python timestamp conversion.

Background CSV/XLSX exports use the same configured private storage backend and a separate Celery queue. `DATEPIPETTE_EXPORT_MEMORY_LIMIT_MB`, `DATEPIPETTE_EXPORT_THREADS`, `DATEPIPETTE_EXPORT_TEMP_LIMIT_MB`, `DATEPIPETTE_EXPORT_MAX_ROWS`, and `DATEPIPETTE_EXPORT_FETCH_ROWS` bound local candidate generation. Files are logically inaccessible 24 hours after success; physical cleanup is not yet implemented. S3-backed downloads keep the DatePipette five-minute capability check and then redirect to an equally short-lived presigned private-object URL.

`DATEPIPETTE_OBJECT_STORAGE_BACKEND` selects `local` or `s3`. S3 additionally requires `DATEPIPETTE_S3_BUCKET`, `DATEPIPETTE_S3_ACCESS_KEY_ID`, and `DATEPIPETTE_S3_SECRET_ACCESS_KEY`; compatible installations can set `DATEPIPETTE_S3_ENDPOINT_URL`, region, and path/virtual addressing. Production requires the S3 backend and rejects non-HTTPS custom endpoints. Optional `DATEPIPETTE_S3_SERVER_SIDE_ENCRYPTION=AES256` asks the provider for server-side encryption.

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

Browser authentication uses an HTTP-only session cookie. State-changing requests also send the readable `datepipette_csrf` cookie value in the `X-CSRF-Token` header. Production forces secure cookies even if local configuration attempts to disable them.

The bootstrap administrator creates further local accounts through `POST /api/v1/users`. User creation and mutations are administrator-only and CSRF-protected. DatePipette prevents supported API operations from disabling or demoting the final active administrator.

PostgreSQL connection passwords and optional CA certificates require `DATEPIPETTE_CONNECTION_ENCRYPTION_KEY`, a URL-safe base64 encoding of exactly 32 random bytes. The built-in default is only for local development and is rejected in production. Generate a key without storing it in shell history:

```bash
uv run --package datepipette python -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("="))'
```

Back up this key separately from metadata PostgreSQL. Losing it makes stored source credentials unrecoverable.

Stop local services without deleting their volumes:

```bash
docker compose --env-file .env -f deploy/compose/compose.yaml stop
```

Compose defaults are development-only. Override `DATEPIPETTE_POSTGRES_PASSWORD`, MinIO credentials, and related variables through an explicit Compose environment file for any shared environment. Application settings use the `DATEPIPETTE_` prefix; important values include `ENVIRONMENT`, `LOG_LEVEL`, `HOST`, `PORT`, `DOCS_ENABLED`, `METADATA_DATABASE_URL`, `REDIS_URL`, `READINESS_TIMEOUT_SECONDS`, `SESSION_TTL_SECONDS`, `SESSION_COOKIE_SECURE`, `LOGIN_RATE_LIMIT_ATTEMPTS`, `LOGIN_RATE_LIMIT_WINDOW_SECONDS`, `CONNECTION_ENCRYPTION_KEY`, `SOURCE_CONNECT_TIMEOUT_SECONDS`, `SOURCE_STATEMENT_TIMEOUT_SECONDS`, `REFRESH_STATEMENT_TIMEOUT_SECONDS`, `REFRESH_BATCH_ROWS`, `INSTALLATION_TIMEZONE`, `SCHEDULER_POLL_SECONDS`, `SCHEDULER_BATCH_SIZE`, `OBJECT_STORAGE_BACKEND`, `SNAPSHOT_STORAGE_PATH`, the `S3_*` settings, and the `SNAPSHOT_QUERY_*` limits documented above.

API documentation is enabled by default only in the `development` environment. Connection URLs are represented as secrets and must never be printed or committed.

## Verification

Run the same essential checks as CI from the repository root:

```bash
uv lock --check
uv run --package datepipette ruff format --check backend
uv run --package datepipette ruff check backend
uv run --package datepipette mypy
uv run --package datepipette pytest
uv run --package datepipette alembic -c backend/alembic.ini heads
uv run --package datepipette alembic -c backend/alembic.ini check
uv run --package datepipette python tools/export_openapi.py
git diff --exit-code -- docs/openapi.json
docker compose --env-file .env -f deploy/compose/compose.yaml config --quiet
```

To include the real MinIO round-trip tests, export the `DATEPIPETTE_TEST_S3_ENDPOINT`, `DATEPIPETTE_TEST_S3_BUCKET`, `DATEPIPETTE_TEST_S3_ACCESS_KEY_ID`, and `DATEPIPETTE_TEST_S3_SECRET_ACCESS_KEY` values for the running development service before invoking `pytest`.

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

By intentionally submitting a contribution for inclusion in DatePipette, you agree that it may be distributed under the Apache License 2.0, consistent with the repository license.
