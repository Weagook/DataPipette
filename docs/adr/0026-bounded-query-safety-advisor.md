# ADR-0026: Bounded PostgreSQL Query Safety Advisor

- Status: Accepted
- Date: 2026-10-11

## Context

Dataset validation already asks PostgreSQL for a plain JSON execution plan, but previously retained only the root estimate and emitted one fixed large-result warning. That cannot explain expensive work below the root, distinguish advisory risk from a policy denial, or show whether relation statistics and indexes visible to the source role support the estimate.

Planner estimates are useful signals, not measured runtime. The advisor must not execute a potentially expensive business query merely to decide whether that query is safe, and DatePipette must never alter a customer's source schema.

## Decision

- Analyze the entire tree returned by `EXPLAIN (FORMAT JSON)` without `ANALYZE`. Store a bounded summary containing root rows/cost plus node, join, sort, maximum-row, sequential-scan, and sort-row counts; do not persist the raw plan.
- During the same read-only source transaction, collect bounded metadata only for the definition's already-authorized relations: planner row estimate, relation/index bytes, valid ready index count, and the latest manual or automatic analyze timestamp.
- Return stable finding codes with `info`, `warning`, `blocked`, or existing structural `error` severity. Numeric findings include observed and threshold values. Relation-specific findings carry a stable visual relation ID when one exists.
- Treat planner row, total-cost, and join-count hard limits as `blocked`. A blocked validation artifact is persisted for diagnosis but cannot be previewed, refreshed, scheduled, or published. Warning and info findings remain advisory.
- Configure foundation thresholds through validated `DATEPIPETTE_QUERY_SAFETY_*` installation settings. Warning thresholds must be lower than their corresponding block thresholds.
- Describe only observable evidence. `no_visible_index_for_large_scan` means no valid ready index was visible for a relation participating in a large sequential scan; it does not claim that adding an index will improve the query.
- Never issue `EXPLAIN ANALYZE`, DDL, `ANALYZE`, or index creation. Persistent administrator policies, operation-specific limits, privileged overrides, and actual-versus-estimated execution feedback remain separate later slices.

## Consequences

- The dataset editor can present actionable risk before source execution and can distinguish a warning from a policy block.
- Validation artifacts and the API grow additively. Readers supply defaults for older JSON documents, so no metadata migration is required.
- Catalog visibility and statistics quality constrain the advice. Missing or stale statistics are surfaced explicitly; estimates must never be presented as certainty.
- Installation configuration is intentionally coarse. A later metadata-backed administrator policy can replace it without changing stable finding codes or the safety boundary.

## Alternatives considered

- **Use `EXPLAIN ANALYZE`:** rejected because it executes the business query and defeats pre-execution safety.
- **Persist the complete PostgreSQL plan:** rejected because plans may be large, expose unnecessary source detail, and are unstable across PostgreSQL versions.
- **Automatically create a recommended index:** rejected because DatePipette's source role is read-only and source DDL belongs to the database owner.
- **Block every large sequential scan:** rejected because sequential scans can be optimal; the first policy blocks only explicit aggregate row, cost, and join thresholds while scan/index/statistics findings remain explainable advice.
