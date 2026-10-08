# Repository guidance

## Product

DatePipette is an API-first, snapshot-based BI platform for PostgreSQL. The backend is a headless product core; the official frontend is a client of the same public API used by integrations.

The repository is currently pre-alpha and documentation-first. Do not describe planned behavior as implemented.

## Sources of truth

- Read `docs/PRODUCT.md` for product intent and non-goals.
- Read `docs/MVP.md` when changing first-release scope.
- Read `docs/DOMAIN_MODEL.md` for domain terminology and lifecycle.
- Read `docs/ARCHITECTURE.md` for component boundaries and data flows.
- Read `docs/API_GUIDELINES.md` for public API work.
- Read `docs/SECURITY.md` for authentication, authorization, SQL, secrets, snapshots, or exports.
- Read the relevant ADR before changing an accepted architecture decision.

Only load the documents relevant to the current task. If documents conflict, stop and surface the conflict rather than silently choosing one.

## Engineering agreements

- Prefer a complete vertical slice over broad scaffolding.
- Keep the domain independent of FastAPI, Celery, SQLAlchemy, Redis, S3, and DuckDB.
- Keep backend behavior accessible through the public API; do not hide product logic only in the frontend.
- Treat OpenAPI as a reviewed contract and verify generated-client drift in CI once generation exists.
- Enforce authorization in the backend on every protected operation.
- Never log or return passwords, connection secrets, session secrets, or API token plaintext.
- Keep source-database operations read-only, bounded, cancellable, and authorized.
- Preserve the last successful published definition and snapshot when draft publication fails.
- Do not add a production dependency without explaining why existing dependencies or the standard library are insufficient.
- Add or update tests and documentation whenever behavior changes.

## Tooling direction

- Backend: Python 3.12 managed with `uv`.
- Python quality: Ruff, mypy, and pytest.
- Frontend: React and TypeScript; package-manager commands will be documented when the frontend scaffold is created.
- Local infrastructure: Docker Compose.

Do not invent commands that the repository does not yet provide. When adding a command, document it in `CONTRIBUTING.md` in the same change.

## Planning and completion

Before starting a non-trivial task, read `.local/ACTIVE_PLAN.md` if it exists. Keep that file current while working: record the objective, scope, milestones, decisions, validation evidence, and the next concrete action. It is local working memory and must never be committed.

Use one active plan for one branch and one coherent outcome. Replace the plan only after the current task is complete or deliberately abandoned. The reusable plan format is documented in `docs/plans/README.md`.

Move lasting knowledge out of the local plan before finishing: product behavior belongs in the relevant product document, architectural decisions in an ADR, contributor commands in `CONTRIBUTING.md`, and future work in `docs/ROADMAP.md` or an issue.

A change is complete only when its observable behavior works, relevant tests pass, migrations and generated contracts are consistent, security boundaries are checked, and documentation reflects the result.

## Repository hygiene

- `RAW_IDEA.md` is private discovery material and must remain ignored.
- `.local/` contains private working state and must remain excluded through `.git/info/exclude`.
- Never commit credentials, generated export files, snapshots, local databases, or environment-specific secrets.
- Preserve unrelated user changes in a dirty working tree.
- Do not create `wip`, `fix typo`, or checkpoint commits on shared history. Commit only coherent, verified changes using the repository conventions in `docs/GIT_WORKFLOW.md`.
