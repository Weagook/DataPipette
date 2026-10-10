# ADR-0018: Versioned visual join trees and advisory foreign-key suggestions

- Status: Accepted
- Date: 2026-10-10

## Context

The first visual-definition format deliberately stores one relation. The no-code builder now needs reusable detailed-row datasets spanning several PostgreSQL relations while preserving the ability to reopen and edit the exact user intent. Arbitrary join graphs and SQL expressions would make ordering, authorization, validation, and safe compilation ambiguous. Existing version-1 drafts must remain readable without an implicit rewrite.

PostgreSQL foreign keys are useful suggestions, but they are not always the desired analytical relationship and may be absent. Accepting a suggestion must therefore remain an explicit editor action rather than hidden mutation of a dataset.

## Decision

Schema version 1 remains one relation with no joins. Schema version 2 stores one to ten ordered, aliased source relations and zero to nine explicit joins. The first relation is the root. Every later relation is introduced exactly once by an ordered join whose left relation is already connected and whose right relation is new. The resulting structure is a connected acyclic rooted tree.

Each relation, field, and join has a stable UUID. Relation aliases are unique without case sensitivity. A join has:

- type `inner` or `left`;
- explicit left and right relation UUIDs; and
- one to eight ordered equality conditions, each containing one left and one right source-column name.

Cross joins, arbitrary expressions or values, cycles, disconnected relations, and other join types are rejected structurally. A physical relation may appear more than once through different relation UUIDs and aliases, which supports explicit self-joins.

The structured JSON remains the source of truth. SQL is compiled deterministically with static templates and Psycopg identifiers for every schema, relation, alias, column, and output name. No definition field is interpreted as a SQL fragment.

Live validation inspects every relation and its visible columns, checks selected fields and join columns, then runs plain `EXPLAIN (FORMAT JSON)` without `ANALYZE`. Stable findings identify the affected relation, field, or join. Fields from the right side of a `LEFT JOIN` receive conservative nullable output metadata so preview and Parquet publication can represent null-extended rows safely.

Every source-touching boundary authorizes the complete relation set, including create, replacement, validation, preview, refresh request, and worker execution. Access to only a subset is insufficient.

A bounded read-only catalog operation accepts up to ten caller-identified relation references and returns foreign keys connecting those authorized relations. It preserves composite-key column order and maps suggestions back to caller UUIDs. Suggestions are transient and advisory; accepting one means saving a normal explicit join in the dataset definition.

## Consequences

- Existing version-1 JSON remains valid and is not rewritten on read.
- The UI and API can reconstruct aliases, join order, type, and conditions without reverse-engineering generated SQL.
- Deterministic tree order makes SQL generation and authorization explanation straightforward, at the cost of excluding cyclic and arbitrary graph plans.
- Ten relations and eight conditions per join bound validation work, request size, and generated SQL complexity.
- Foreign-key suggestions can accelerate authoring but never silently change semantics.
- A relation-scope revocation blocks the whole joined source operation while leaving prior snapshots under independent dataset ACL control.
- Conservative nullability may describe some inner-join descendants as nullable even when PostgreSQL eliminates null-extended rows; this is safe for serialization and can be refined later.

## Alternatives considered

- **Store generated join SQL:** rejected because it loses UI intent and widens the injection and migration surface.
- **Arbitrary join graph:** rejected because cycles and ordering require a more complex semantic planner before there is a product need.
- **Normalize every relation and join into metadata tables:** rejected because versioned JSON remains the simpler atomic editor contract.
- **Automatically add joins from foreign keys:** rejected because database constraints do not prove the analyst's intended reporting semantics.
- **Rewrite version 1 to version 2 on read:** rejected because reads must not create hidden draft mutations or revision changes.
- **Allow arbitrary join predicates:** deferred until a typed expression model can preserve safety and explainability.
