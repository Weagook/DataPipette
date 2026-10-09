# ADR-0010: Revision-bound non-executing dataset validation

- Status: Accepted
- Date: 2026-10-09

## Context

A structurally valid visual definition can still reference a removed relation, a renamed column, or a source type that changed after the draft was saved. The editor needs an explicit check before preview and publication, but that check must not read or return business rows. Generated SQL and inferred schema are derived from one draft revision and become unsafe to reuse after an edit.

## Decision

- Keep draft saving offline. Perform source validation only through an explicit operation requiring the current draft ETag.
- Open the configured source in a bounded read-only transaction and re-check effective relation and column visibility against `pg_catalog`.
- Build the one-table `SELECT` solely with static templates and Psycopg `Identifier` objects for schema, relation, alias, source columns, and output names.
- Run `EXPLAIN (FORMAT JSON)` without `ANALYZE`. Store only root node type, estimated rows, startup cost, and total cost; never store the raw plan.
- Store the compiled SQL, typed result schema, compact plan summary, stable findings, timestamp, and validated revision as one JSONB artifact on the draft version.
- Clear the artifact when the definition changes. Lock and compare the draft revision before persisting a result so validation cannot race an edit.
- Represent missing source relations or fields as an `invalid` result with stable findings. Treat connectivity, credentials, TLS, timeout, secret decryption, and malformed source responses as redacted operational failures that leave the previous artifact intact.
- Classify an otherwise valid plan with at least one million estimated output rows as `valid_with_warnings`. This is an initial advisory, not a publication policy or a claim about actual runtime.
- Audit only the revision, state, field count, and finding codes. Exclude generated SQL, definitions, credentials, raw errors, plans, and business values.

## Consequences

- The UI can show whether the exact JSON being edited was validated and can reconstruct PostgreSQL types without previewing data.
- Correct identifier quoting supports mixed case, spaces, and reserved words without widening the SQL-injection surface.
- Plain `EXPLAIN` supplies estimates quickly but does not prove runtime, actual row count, or index suitability. Later Query Safety Advisor work must retain this distinction.
- The generated SQL intentionally contains source object names and is therefore available only through the same authorized draft API; it is not copied into logs or audit events.
- A source outage does not destroy a prior artifact, but clients can distinguish its revision from the current draft and should show the failed attempt separately from validity.

## Alternatives considered

- `EXPLAIN ANALYZE` was rejected because it executes the business query and can impose the very load this guard is meant to avoid.
- Previewing one row to infer types was rejected because PostgreSQL catalog metadata already supplies types and a row preview crosses the business-data boundary.
- Persisting raw plans was rejected because they are verbose, source-specific, potentially sensitive, and unnecessary for the first contract.
- Treating missing objects as `404` was rejected because the dataset exists; source drift is a validation outcome that the editor must render field by field.
