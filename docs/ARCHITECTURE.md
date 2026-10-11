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

Catalog discovery is a live adapter query rather than a metadata replica. The API opens bounded read-only source transactions, queries static `pg_catalog` statements, intersects the connected role's schema/table/column privileges with the analyst's effective exact connection scopes, and returns only structural metadata. Scope filtering occurs before pagination, system schemas are hidden by default, and foreign-key targets outside the effective scope are omitted. A bounded join-suggestion operation maps direct and composite foreign keys among up to ten authorized caller-identified relations without persisting a catalog copy.

### Datasets

Visual and SQL definitions, validation, compilation, draft/publish lifecycle, result schema, display settings, dependency analysis, and schedules.

Validation is synchronous and supports version-1 single-relation and version-2 multi-relation visual definitions plus advanced SQL definitions. Visual drafts compile an ordered acyclic `INNER JOIN`/`LEFT JOIN` tree. SQL drafts preserve editor text while deriving a canonical statement and schema-qualified base-relation set from a PostgreSQL-dialect AST. Both paths re-check live relation visibility, obtain a non-executing PostgreSQL JSON plan, verify result metadata, and store a compact artifact tied to the draft revision.

The Query Safety Advisor recursively summarizes that non-executing plan and combines it with bounded visible catalog metadata for the authorized source relations. Stable findings explain high estimated result rows/cost, join count, large sequential scans and sorts, absent visible indexes, and stale planner statistics. Installation thresholds may warn or block; blocked drafts cannot proceed to preview or refresh. The advisor stores evidence rather than the raw plan, treats estimates as uncertain, and never runs `EXPLAIN ANALYZE` or source DDL.

Immutable published/superseded versions are available through bounded owner-authorized history APIs. Revision-bound impact analysis is metadata-only: it compares stable field UUIDs and validated result contracts against the active published version, classifies compatible/breaking changes, records redacted counts, and never re-executes source SQL. Dependency collections deliberately remain empty until charts provide real references.

The first preview slice is also synchronous but strictly bounded: the API requires matching validation, recompiles the definition, binds a small `LIMIT`, streams from a server-side cursor, and returns JSON-safe rows under an encoded-byte budget. It persists only a redacted audit event and never creates a snapshot.

Refresh execution is asynchronous and durable. The API transaction records idempotent intent in metadata PostgreSQL before publishing a UUID-only Celery message. A worker transaction claims that row, rejects stale revisions, reloads the requesting user's effective relation scope, and enforces one running plus one queued successor per dataset. Redis is transport rather than authoritative job state. The executor recompiles either definition kind from its authoritative JSON document, streams named-cursor batches into typed Parquet, validates the completed candidate, publishes it through the configured local or S3-compatible storage adapter, verifies the published object, and only then atomically activates snapshot metadata.

Scheduled refresh uses an independent database-polling process. Bounded `SKIP LOCKED` claims advance due interval/daily schedules, target only the active published version, and create or coalesce the same durable refresh-run resources used by manual requests. Every poll also republishes queued UUIDs whose prior Redis dispatch did not complete, so scheduler restart heals durable broker-publication gaps without replaying every missed cadence.

### Execution

Source query execution, bounded streaming, local candidate creation, verified local or S3-compatible Parquet publication, basic result statistics, and snapshot activation are implemented for version-1 single-relation visual definitions, version-2 joined visual definitions, and advanced read-only SQL definitions. Owner-authorized cancellation is durable and cooperative, with cancellation checked before atomic publication. Explicit transient source/storage failures use durable bounded exponential retries dispatched by the scheduler; renewable running leases recover worker loss without making Redis authoritative, while permanent failures remain terminal. Automatic cleanup removes eligible superseded objects after a grace period and reconciles sufficiently old unreferenced generated objects. Progress and broader resource policies remain later execution capabilities.

### Query

Typed snapshot table filters, projection, deterministic cursor pagination, bounded DuckDB execution, and result serialization are implemented for local and S3-backed snapshots. S3 objects are materialized into unique ephemeral files and verified against persisted size and SHA-256 before DuckDB reads them. A bounded metadata-only freshness projection reports unpublished, refreshing, ready, stale-fallback, and failed states without touching source PostgreSQL, DuckDB, or object storage. An authorized exploration descriptor projects ordered saved display settings and type-compatible filter/sort capabilities from the active immutable version; row responses reuse that display contract and freshness projection. Chart query compilation, aggregation, direct or ranged S3 reads, distributed concurrency controls, and production benchmarks remain future work.

### Visualization

Chart specifications, type validation, dashboard layouts, chart dependency tracking, and invalid-state reporting.

### Export

Snapshot-bound asynchronous CSV/XLSX generation, durable job history, private local or S3-compatible artifact storage, short-lived capability issuance, and automatic physical cleanup are implemented. The worker validates the stored typed query against the captured snapshot schema, streams bounded DuckDB batches into a local candidate file, neutralizes formula-like strings, and publishes the artifact through the configured storage adapter. Logical expiry is enforced after 24 hours. A valid DatePipette capability returns a local file or redirects to an equally short-lived presigned private-object URL. Cleanup removes the artifact after expiry plus a delivery grace while retaining job history.

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
4. Materialize and verify the captured snapshot when it is remote, then stream the deterministic DuckDB result into a private CSV or XLSX candidate under row and resource limits.
5. Publish the candidate to local or S3-compatible storage, verify the stored object, atomically publish file metadata, retain it logically for 24 hours, and expose safe job history.
6. Issue a reissuable encrypted five-minute download capability after dataset export authorization and export-job ownership checks; resolve it to a local response or a five-minute presigned S3 redirect.

## Failure model

- Refresh failure retains the last successful snapshot.
- If no successful snapshot exists, the dataset has no readable rows and exposes a failed state.
- Dashboard charts fail independently.
- Cleanup jobs are idempotent and retryable.
- Cleanup never removes an active snapshot or a superseded snapshot required by queued, running, or unexpired exports.
- Old unreferenced objects are reconciled through bounded prefix scans after a publication-safety grace.
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
