# ADR-0021: Advanced read-only SQL dataset definitions

- Status: Accepted
- Date: 2026-10-11

## Context

The visual builder deliberately exposes a bounded relational model, but advanced analysts also need PostgreSQL expressions, CTEs, and query shapes that the no-code contract cannot yet represent. The SQL must remain editable, use the same draft/validation/preview/refresh lifecycle as visual datasets, and never bypass connection scope or source safety controls.

Text inspection alone cannot reliably distinguish a read query from DDL, DML, multiple statements, locking clauses, or data-modifying CTEs. Conversely, a parser is not a database security boundary: PostgreSQL functions, views, rewrite rules, and parser differences mean that the configured source role and read-only transaction remain authoritative.

## Decision

- Add a discriminated dataset-definition document. `kind: visual` preserves the existing structured contract; `kind: sql` stores schema version 1, the original statement, and one to 500 stable output-field UUIDs with names and display settings. Legacy visual documents without `kind` remain readable.
- Accept exactly one PostgreSQL-dialect `SELECT`/query expression, optionally with read-only CTEs. Reject empty or oversized input, multiple statements, DDL/DML, data-modifying CTEs, `SELECT INTO`, row-locking clauses, external parameters, and cross-database or unqualified base relations.
- Parse the statement with SQLGlot and its PostgreSQL dialect. Persist the trimmed user text unchanged for editor round-trips; derive a canonical execution statement and schema-qualified base-relation set from the AST on every document load.
- Use SQLGlot because its MIT license is compatible with this Apache-2.0 project. A PostgreSQL-native parser with GPL terms was rejected for this dependency boundary.
- Authorize every discovered base relation at create, replace, validation, preview, refresh request, and worker execution. CTE and subquery aliases are not treated as source relations.
- Validate in the existing timed read-only source transaction. Check live relation visibility, run `EXPLAIN (FORMAT JSON)` without `ANALYZE`, then wrap the query with `LIMIT 0` to obtain PostgreSQL result metadata without returning rows. Declared field names must exactly match result-column order and names; SQL output nullability is conservative.
- Preview and refresh reparse the persisted definition and wrap its canonical statement as a subquery. Preview adds its existing bound limit and byte budget; refresh streams through the existing Parquet publication pipeline. Validation SQL is evidence, not executable authority.
- Reuse the current ETag, audit, scheduling, freshness, snapshot rows, typed filters, exports, ACL, and atomic publication behavior for both definition kinds.

## Consequences

- The same dataset can be reopened and edited from its complete JSON source while downstream consumers keep stable field UUIDs.
- Advanced SQL gains CTEs and PostgreSQL expressions without creating a separate execution or storage subsystem.
- Schema-qualified relations make scope checks deterministic, but SQL copied from tools that rely on `search_path` must be qualified before it is accepted.
- Validation can detect syntax, missing relations, changed output names, and planner-size warnings. It cannot prove semantic correctness or fully expand lineage hidden behind views or functions.
- Read-only least-privilege PostgreSQL credentials, trusted function privileges, statement timeouts, and bounded fetches remain mandatory. AST validation is defense in depth.
- Canonicalization may change formatting while the exact user-authored statement remains the editor source of truth.

## Alternatives considered

- **Store only canonical SQL:** rejected because comments and user formatting are part of a useful editor round-trip.
- **Execute the stored validation artifact:** rejected because it can become stale and would create a second source of authority.
- **Regex or token filtering:** rejected because nested CTEs, comments, quoting, and multi-statement input require structural analysis.
- **Allow unqualified relations through `search_path`:** rejected because authorization and lineage would become environment-dependent.
- **Infer stable field IDs from result names:** rejected because renaming or reordering would silently change downstream identity.
- **Use a GPL PostgreSQL parser dependency:** rejected to preserve a straightforward Apache-2.0 distribution boundary.
