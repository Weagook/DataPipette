# ADR-0009: Versioned JSONB visual dataset definitions

- Status: Accepted
- Date: 2026-10-09

## Context

The No-Code builder needs a durable representation that can reconstruct the editing interface, survive label changes, evolve across product releases, and compile deterministically to SQL later. Normalizing every UI node into relational metadata tables would make small definition changes migration-heavy, while storing only generated SQL would discard user intent. Editing must not alter the version and snapshot currently serving readers.

## Decision

- Store the complete visual definition as a PostgreSQL JSONB document on a dataset-version row.
- Treat the structured definition, not generated SQL, as the source of truth.
- Require a top-level integer `schema_version`; version 1 contains exactly one source relation, 1–500 selected fields, display metadata, and an empty joins collection.
- Give source relations and output fields stable UUIDs. Labels, display formats, and output names can change without changing those identities.
- Keep stable resource metadata such as dataset name, owner, connection, and active-version reference in relational columns with foreign keys and indexes.
- Maintain at most one mutable draft per dataset. Future published and superseded versions are immutable.
- Replace the entire JSONB definition on save. Do not mutate nested ORM values or JSONB paths in place.
- Require an optimistic integer revision for replacement. A stale revision fails instead of overwriting another editor's changes.
- Permit structurally valid drafts to be saved while the source is unavailable. Source authorization, catalog validity, compilation, and query safety belong to explicit later validation and publication steps.
- Bound field count and serialized definition size. Audit only safe summaries such as schema version, field count, and revision rather than the document itself.
- Do not add a JSONB GIN index until an implemented query requires searching inside definitions.

## Consequences

- The official frontend and third-party clients can retrieve the document and reconstruct the visual editor without reverse-engineering SQL.
- A definition-format change requires a documented schema migration or compatible reader rather than an unversioned payload change.
- Whole-document replacement aligns with SQLAlchemy JSON change detection and makes revision comparison straightforward.
- Stable field IDs give future charts a dependency identity independent of user-facing labels.
- Relational queries can efficiently list and authorize datasets without inspecting JSONB.
- Draft creation is available even during source downtime, but users must not interpret a saved draft as validated or publishable.

## Alternatives considered

- Storing generated SQL alone was rejected because SQL cannot reliably reproduce the original No-Code choices and stable field identities.
- Fully normalizing relations, fields, joins, and presentation nodes was rejected for the first definition format because it couples UI evolution to many metadata migrations and transactions.
- In-place JSONB path updates were rejected because whole-document validation and optimistic replacement provide a simpler consistency boundary.
- Treating every draft edit as an immutable version was deferred because autosave would create excessive operational history before publication and audit-retention behavior are designed.
