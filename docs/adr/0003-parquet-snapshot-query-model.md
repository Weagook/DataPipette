# ADR-0003: Parquet snapshot query model

- Status: Accepted
- Date: 2026-10-08

## Context

Interactive BI traffic must not repeatedly execute against operational PostgreSQL databases. Dataset results may contain millions of rows, making Redis or metadata PostgreSQL unsuitable as the primary tabular result store.

## Decision

Dataset refresh streams source results into immutable Parquet snapshots. Production stores snapshots in private S3-compatible storage; development and tests may use a local filesystem adapter. Interactive table and chart queries run against the active snapshot through an analytical execution layer, initially DuckDB.

Each dataset exposes one active snapshot. The previous snapshot remains readable until replacement publication succeeds, then is deleted asynchronously. Historical snapshots are not retained.

## Consequences

- Source PostgreSQL load is concentrated in preview and refresh workflows.
- Refresh, storage, and publication require a two-phase, failure-safe workflow.
- Query performance benefits from Parquet projection and predicate pushdown.
- Local filesystem storage is not valid for horizontally scaled production.
- Old point-in-time data cannot be reproduced after snapshot and export expiry.
- DuckDB and S3 concurrency must be validated through an explicit prototype and benchmark.

## Alternatives considered

- Redis was rejected as the primary result store because large tabular data would consume excessive RAM.
- Metadata PostgreSQL was rejected for snapshot rows because it would couple operational metadata and analytical storage.
- Querying source PostgreSQL on every interaction was rejected because it exposes operational systems to unpredictable BI workloads.
