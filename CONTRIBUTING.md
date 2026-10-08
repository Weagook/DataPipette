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
- Docker with Compose v2 for PostgreSQL and Redis.

Install the locked workspace, start local dependencies, and apply metadata migrations:

```bash
uv sync --locked --all-packages --all-groups
docker compose -f deploy/compose/compose.yaml up -d --wait
uv run --package datepipette alembic -c backend/alembic.ini upgrade head
```

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

The API listens on `http://127.0.0.1:8000` by default. Useful endpoints are:

- `GET /api/v1/health/live` for process liveness;
- `GET /api/v1/health/ready` for PostgreSQL and Redis readiness;
- `POST /api/v1/auth/sessions` for browser sign-in;
- `GET /api/v1/auth/me` for the current local user;
- `DELETE /api/v1/auth/sessions/current` for sign-out;
- `/docs` and `/openapi.json` for the development API contract.

Browser authentication uses an HTTP-only session cookie. State-changing requests also send the readable `datepipette_csrf` cookie value in the `X-CSRF-Token` header. Production forces secure cookies even if local configuration attempts to disable them.

Stop local dependencies without deleting their volumes:

```bash
docker compose -f deploy/compose/compose.yaml stop
```

Compose defaults are development-only. Override `DATEPIPETTE_POSTGRES_PASSWORD` and related variables through the shell or an explicit Compose environment file for any shared environment. Application settings use the `DATEPIPETTE_` prefix; important values include `ENVIRONMENT`, `LOG_LEVEL`, `HOST`, `PORT`, `DOCS_ENABLED`, `METADATA_DATABASE_URL`, `REDIS_URL`, `READINESS_TIMEOUT_SECONDS`, `SESSION_TTL_SECONDS`, `SESSION_COOKIE_SECURE`, `LOGIN_RATE_LIMIT_ATTEMPTS`, and `LOGIN_RATE_LIMIT_WINDOW_SECONDS`.

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
docker compose -f deploy/compose/compose.yaml config --quiet
```

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
