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

### Connections and catalog

Encrypted PostgreSQL connection configuration, health checks, schema introspection, table and column metadata, foreign-key suggestions, and connection visibility policies.

Catalog discovery is currently a live adapter query rather than a metadata replica. The API opens a bounded read-only source transaction, queries static `pg_catalog` statements, filters results with the connected role's effective schema/table/column privileges, and returns only structural metadata. System schemas are hidden by default. Connection-grant allowlists will narrow this database-visible set when that later authorization slice is implemented.

### Datasets

Visual and SQL definitions, validation, compilation, draft/publish lifecycle, result schema, display settings, dependency analysis, and schedules.

The first validation slice is synchronous and single-table: it re-checks live catalog visibility, composes identifiers safely, obtains a non-executing PostgreSQL JSON plan, and stores a compact artifact tied to the draft revision. It neither previews rows nor replaces the later background refresh/publication pipeline.

The first preview slice is also synchronous but strictly bounded: the API requires matching validation, recompiles the definition, binds a small `LIMIT`, streams from a server-side cursor, and returns JSON-safe rows under an encoded-byte budget. It persists only a redacted audit event and never creates a snapshot.

### Execution

Source query execution, bounded streaming, Parquet writing, result statistics, cancellation, resource limits, snapshot publication, and cleanup.

### Query

Typed table filters, pagination, chart query compilation, DuckDB execution, result serialization, and freshness metadata.

### Visualization

Chart specifications, type validation, dashboard layouts, chart dependency tracking, and invalid-state reporting.

### Export

Asynchronous CSV/XLSX generation, private artifact lifecycle, signed URL issuance, and expiry cleanup.

### Audit and operations

Audit events, structured logs, metrics, trace propagation, health checks, and administrative settings.

## Data ownership

| Store | Owns | Must not own |
|---|---|---|
| Metadata PostgreSQL | identities, ACLs, connections, dataset definitions, chart/dashboard config, jobs, audit events | snapshot rows, token plaintext, connection plaintext |
| Redis | Celery transport/results as configured, short-lived locks, coordination, ephemeral cache | authoritative metadata, large tabular results |
| S3-compatible storage | Parquet snapshots and temporary export files | authoritative ACLs or secrets |
| Source PostgreSQL | company business data | DatePipette metadata |

## Dataset publication flow

```text
Save draft
   -> validate structure and authorization
   -> preview with strict limits
   -> analyze chart impact
   -> enqueue refresh
   -> acquire per-dataset distributed lock
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
