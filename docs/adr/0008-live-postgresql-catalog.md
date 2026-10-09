# ADR-0008: Live privilege-filtered PostgreSQL catalog discovery

- Status: Accepted
- Date: 2026-10-09

## Context

Visual dataset creation needs schemas, selectable relations, column types, primary keys, and foreign-key suggestions from a configured PostgreSQL source. Copying this metadata into DatePipette would introduce synchronization, invalidation, and stale-definition behavior before dataset drafts and publication exist. Catalog discovery must also respect the least-privileged PostgreSQL role supplied for a connection and must not become an arbitrary SQL facility.

## Decision

- Read structural metadata live from PostgreSQL `pg_catalog`; do not persist or cache a catalog replica in this slice.
- Use static application-owned statements. Bind schema names, relation names, cursors, and limits as parameters.
- Run every catalog request in the same bounded read-only source-session boundary used by connection checks: bounded connection time, transaction-level read-only mode, and a statement timeout.
- List only schemas for which the connected role has effective `USAGE` and relations for which it has effective `SELECT`.
- Return only selectable columns; omit foreign-key targets unless the connected role can also use the target schema and select the target relation.
- Hide `pg_*` and `information_schema` schemas by default. Administrators may explicitly include them for diagnosis.
- Support tables, partitioned tables, views, materialized views, and foreign tables as relations.
- Preserve constraint-column order when reporting composite primary and foreign keys.
- Use opaque name cursors and bounded pages for schema and relation collections.
- Treat a missing and a privilege-hidden relation identically. Collapse timeout, network, TLS, authentication, and catalog-query failures into a safe source-unavailable API problem.
- Keep endpoints administrator-only until connection grants and DatePipette schema/table allowlists are implemented.

## Consequences

- Discovery reflects current PostgreSQL DDL and grants without a refresh job or invalidation protocol.
- Catalog latency and availability depend on the source database, so clients must handle safe transient failures and retry.
- DatePipette stores no additional source structure that could become stale or leak after a PostgreSQL grant is revoked.
- The configured PostgreSQL role remains a primary security boundary. Future connection allowlists must further narrow, never widen, this database-visible set.
- Dataset publication will need to capture its own validated result schema; a live catalog response is discovery input, not a durable dataset contract.

## Alternatives considered

- Persisting and periodically synchronizing the entire source catalog was deferred because it creates stale-state and revocation windows without adding value to the first dataset slice.
- `information_schema` alone was rejected because PostgreSQL relation kinds and ordered key mappings are represented more directly and completely in `pg_catalog`.
- Returning every catalog row visible to a metadata query was rejected because catalog presence is not equivalent to effective permission to use the object.
- Accepting user-authored catalog SQL was rejected because discovery needs a small structural contract, not a general query endpoint.
