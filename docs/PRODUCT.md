# Product vision

## Vision

DataPipette is a simple, embeddable BI engine for teams whose business data lives in PostgreSQL. It turns a visual dataset definition or an advanced SQL query into a governed snapshot that can be browsed, exported, visualized, and embedded through a public API.

## Product shape

DataPipette is both:

- a standalone web application for analysts, administrators, and dashboard viewers; and
- a headless backend that external products can integrate through the same public API used by the official frontend.

The backend is the product core. No essential product behavior may exist only in the official frontend.

## Initial audience

DataPipette initially targets single-company deployments such as CRM products, internal banking systems, and other operational platforms backed by PostgreSQL.

### Administrator

Connects PostgreSQL sources, manages installation settings, users, groups, service accounts, access policies, tokens, schedules, and audit retention.

### Analyst

Builds and publishes datasets, explores detailed rows, exports filtered data, creates charts, and assembles dashboards within granted access.

### Viewer

Views shared dashboards and aggregated chart results without gaining automatic access to underlying detailed data.

### Integrating developer

Uses a service account and scoped API token to embed DataPipette capabilities in another application.

## Core jobs to be done

- Connect a PostgreSQL source without exposing its credentials to analysts.
- Build a reusable virtual dataset without writing SQL.
- Use advanced read-only SQL when the visual builder is insufficient.
- Isolate interactive analytics from the operational source database.
- Inspect and export detailed data with typed filters.
- Create persistent analytical charts and compose dashboards.
- Share only the intended level of data access with users and groups.
- Integrate the BI core through a predictable, documented API.
- Understand whether displayed data is current, stale, or unavailable.

## Product principles

### API first

Every official frontend capability is backed by a documented public API. OpenAPI is a contract, not an incidental framework output.

### Private by default

New resources are visible only to their owner and administrators until explicitly shared with users or groups.

### Snapshots protect source systems

Interactive table, chart, and dashboard requests query a published snapshot instead of the source PostgreSQL database. The source is accessed only during validation, preview, and refresh workflows.

### Safe publication

Dataset changes are drafted and validated before publication. A failed refresh must not replace a working definition or snapshot.

### Explicit failure

Errors are represented as states and structured API responses. A stale successful snapshot may remain visible after refresh failure, but the failure and last successful refresh time must be obvious.

### Incremental scope

DataPipette will prefer a complete vertical slice over a broad collection of incomplete features.

## Initial non-goals

- Multi-tenant SaaS within one DataPipette installation
- Data sources other than PostgreSQL
- Cross-connection joins
- Dataset-on-dataset dependencies
- A distributed query engine
- Editing or writing data back to source systems
- Historical snapshot browsing or rollback
- Kubernetes and Helm packaging in the first release
- Enterprise SSO in the first release
