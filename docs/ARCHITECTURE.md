# Architecture

## Architectural style

DatePipette starts as a modular monolith with independently deployable process types:

- a stateless FastAPI application;
- Celery workers for refresh, export, and cleanup jobs;
- a scheduler process for refresh schedules; and
- an independent React frontend.

The modular monolith keeps domain boundaries explicit without introducing network boundaries before they provide value.

## System context

```text
                     +----------------------+
                     | Official React app   |
                     +----------+-----------+
                                |
External applications ----------+---- HTTPS / REST
                                |
                     +----------v-----------+
                     | Stateless FastAPI    |
                     | auth, metadata, API  |
                     +----+----------+------+
                          |          |
              +-----------v--+    +--v----------------+
              | Metadata PG  |    | Redis             |
              | durable state|    | broker/cache/locks|
              +--------------+    +--+----------------+
                                      |
                               +------v-------+
                               | Celery worker|
                               +---+-------+--+
                                   |       |
                        +----------v-+   +-v----------------+
                        | Source PG  |   | Local or S3      |
                        | read-only  |   | Parquet/exports  |
                        +------------+   +------------------+

Snapshot reads: API or worker -> DuckDB -> Parquet storage
```

## Repository layout

```text
DatePipette/
├── backend/
│   ├── src/datepipette/
│   │   ├── api/
│   │   ├── application/
│   │   ├── infrastructure/
│   ├── migrations/
│   └── tests/
├── deploy/
│   └── compose/
├── docs/
└── pyproject.toml
```

The current foundation creates directories only when they own working code. `domain/`, `workers/`, `frontend/`, and `tools/` will be added with the first feature that needs them rather than as empty placeholders.

Dependencies point inward: infrastructure and API adapters may depend on application and domain interfaces; the domain must not depend on FastAPI, Celery, SQLAlchemy, Redis, S3, or DuckDB.

## Backend modules

### Identity and access

Users, groups, roles, service accounts, token lifecycle, sessions, scopes, ACL evaluation, and object ownership.

Groups, human-user memberships, additive dataset grants, and scoped connection grants are stored in metadata PostgreSQL. Protected dataset operations evaluate authoritative grants per request: administrators and owners have full dataset access, analysts receive the union of direct and group permissions, and the viewer role is denied detailed rows and source connections regardless of grants. The implemented permissions separate row browsing, export, grant management, and source authoring. Redis is not an authorization cache, so revocation applies to the next request and is re-checked by refresh workers.

### Connections and catalog

Encrypted PostgreSQL connection configuration, health checks, schema introspection, table and column metadata, foreign-key suggestions, and connection visibility policies.

Catalog discovery is a live adapter query rather than a metadata replica. The API opens a bounded read-only source transaction, queries static `pg_catalog` statements, intersects the connected role's schema/table/column privileges with the analyst's effective exact connection scopes, and returns only structural metadata. Scope filtering occurs before pagination, system schemas are hidden by default, and foreign-key targets outside the effective scope are omitted.

### Datasets

Visual and SQL definitions, validation, compilation, draft/publish lifecycle, result schema, display settings, dependency analysis, and schedules.

The first validation slice is synchronous and single-table: it re-checks live catalog visibility, composes identifiers safely, obtains a non-executing PostgreSQL JSON plan, and stores a compact artifact tied to the draft revision. It neither previews rows nor replaces the later background refresh/publication pipeline.

The first preview slice is also synchronous but strictly bounded: the API requires matching validation, recompiles the definition, binds a small `LIMIT`, streams from a server-side cursor, and returns JSON-safe rows under an encoded-byte budget. It persists only a redacted audit event and never creates a snapshot.

Refresh execution is asynchronous and durable. The API transaction records idempotent intent in metadata PostgreSQL before publishing a UUID-only Celery message. A worker transaction claims that row, rejects stale revisions, reloads the requesting user's effective relation scope, and enforces one running plus one queued successor per dataset. Redis is transport rather than authoritative job state. The local executor recompiles the structured definition, streams named-cursor batches into typed Parquet, validates the completed object, and only then atomically activates snapshot metadata.

### Execution

Source query execution, bounded streaming, local Parquet writing, basic result statistics, and snapshot publication are implemented for one-table visual definitions. S3 storage, cancellation, progress, cleanup, and broader resource policies remain later execution capabilities.

### Query

Typed snapshot table filters, projection, deterministic cursor pagination, bounded DuckDB execution, result serialization, and freshness metadata are implemented for local snapshots. Chart query compilation, aggregation, S3 reads, distributed concurrency controls, and production benchmarks remain future work.

### Visualization

Chart specifications, type validation, dashboard layouts, chart dependency tracking, and invalid-state reporting.

### Export

Snapshot-bound asynchronous CSV/XLSX generation, durable job history, private local artifact storage, and short-lived capability issuance are implemented. The worker validates the stored typed query against the captured snapshot schema, streams bounded DuckDB batches into a candidate file, neutralizes formula-like strings, and atomically publishes the artifact. Logical expiry is enforced after 24 hours; physical cleanup and S3-compatible storage remain future adapters.

### Audit and operations

Audit events, structured logs, metrics, trace propagation, health checks, and administrative settings.

## Data ownership

| Store | Owns | Must not own |
|---|---|---|
| Metadata PostgreSQL | identities, ACLs, connections, dataset definitions, chart/dashboard config, jobs, audit events | snapshot rows, token plaintext, connection plaintext |
| Redis | Celery transport, short-lived locks, coordination, ephemeral cache | authoritative job state, metadata, large tabular results |
| S3-compatible storage | Parquet snapshots and temporary export files | authoritative ACLs or secrets |
| Source PostgreSQL | company business data | DatePipette metadata |

## Dataset publication flow

```text
Save draft
   -> validate structure and authorization
   -> preview with strict limits
   -> analyze chart impact
   -> persist idempotent refresh run
   -> enqueue run UUID through Redis/Celery
   -> transactionally claim the per-dataset execution slot
   -> open read-only source transaction
   -> stream rows into candidate Parquet objects
   -> validate stored output and infer schema/statistics
   -> atomically publish definition + snapshot reference
   -> release readers from old snapshot
   -> enqueue idempotent old-object cleanup
```

Any failure before atomic publication leaves the current definition and snapshot unchanged.

## Interactive query flow

1. Authenticate the user, session, or API token.
2. Authorize direct dataset access or constrained dashboard-context chart access.
3. Load the active snapshot metadata.
4. Compile typed filters, sorting, grouping, and measures into parameterized DuckDB SQL.
5. Execute against Parquet with a timeout and resource limits.
6. Return typed rows plus pagination, snapshot identity, freshness, and safe error metadata.

User-provided values must be bound as parameters. Identifiers must resolve from the published dataset schema rather than being interpolated from arbitrary strings.

## Export flow

1. Authenticate, authorize direct dataset access, require CSRF and an idempotency key.
2. Validate field UUIDs and typed filters/sorts against the active snapshot, then persist a job bound to that snapshot ID.
3. Enqueue only the export UUID through Redis/Celery and atomically claim it in the worker.
4. Stream the deterministic DuckDB result into a private CSV or XLSX candidate under row and resource limits.
5. Atomically publish file metadata, retain it logically for 24 hours, and expose safe job history.
6. Issue a reissuable encrypted five-minute download capability after dataset export authorization and export-job ownership checks.

## Failure model

- Refresh failure retains the last successful snapshot.
- If no successful snapshot exists, the dataset has no readable rows and exposes a failed state.
- Dashboard charts fail independently.
- Cleanup jobs are idempotent and retryable.
- Expired export files are represented explicitly; their former signed URLs are never treated as proof of access.
- Schema drift is surfaced during validation or refresh and never silently changes a published visual definition.

## Scaling model

- API instances are stateless and horizontally scalable behind a load balancer.
- Workers scale separately and may use queues for refresh, export, and maintenance workloads.
- Concurrency limits and distributed locks protect source systems and snapshot publication.
- PostgreSQL, Redis, and S3 are shared production dependencies.
- Local filesystem storage is a development/test adapter only.
- Kubernetes is deferred, but no production behavior may rely on container-local durability.

## Performance baseline

The reference workload targets a 5-million-row, 50-column snapshot on a 4 CPU / 8 GB RAM node, 20 active interactive users, and two concurrent heavy worker jobs. Benchmarks must publish dataset shape, hardware, configuration, and measured operations; row count alone is not a sufficient performance claim.

## Technology direction

### Backend

Python 3.12, uv, FastAPI, Uvicorn, Pydantic v2, SQLAlchemy 2, Alembic, Psycopg 3, Celery, Redis, DuckDB, PyArrow, PostgreSQL, and an S3-compatible client.

### Frontend

React, TypeScript, Vite, TanStack Query, TanStack Table, Apache ECharts, a grid-layout library, and a generated OpenAPI client.

Technology choices remain subject to focused prototypes where risk is material, particularly streaming Parquet creation and concurrent DuckDB reads from S3-compatible storage.
