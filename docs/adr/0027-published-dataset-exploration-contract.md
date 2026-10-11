# ADR-0027: Published dataset exploration contract

- Status: Accepted
- Date: 2026-10-12

## Context

DatePipette already stores field labels, visibility, and display formats in each dataset definition and supports bounded typed snapshot queries. However, a shared client with `view_rows` cannot read an owner's draft or immutable version document, so it has no authorized way to discover field UUIDs and the active published table configuration before issuing its first query. Returning only the selected columns after a query is circular and risks making the official frontend depend on privileged authoring APIs.

The editable draft may differ from the active snapshot. Exploration must follow the last successfully published definition so a failed or incomplete edit cannot change what readers see.

## Decision

- Expose `GET /api/v1/datasets/{dataset_id}/exploration` as a read-only metadata operation requiring the same effective `view_rows` permission as detailed rows.
- Derive the descriptor from the active immutable dataset version and active snapshot. It never reads source PostgreSQL, Parquet rows, or the mutable draft.
- Return ordered fields with stable UUID, logical name, saved label, PostgreSQL type, nullability, visibility, display format, sorting capability, and a stable list of accepted typed filter operators.
- Return up to the row query's 100-field limit of visible fields in definition order as `default_field_ids`. Hidden and additional fields remain in the descriptor and may still be explicitly selected, filtered, sorted, or exported when authorization permits.
- Return snapshot identity, row count, activation timestamp, refresh state, staleness, and the latest safe refresh error so clients can bind their table state to the same immutable data generation.
- Generate operator capabilities from the same application type-family policy used to validate row queries. Unsupported field types expose no filter operators rather than causing descriptor failure.
- Add saved display metadata to every row-query column response. Semicolon-separated Excel-like input remains a frontend concern that becomes the existing explicit `in` value array.

## Consequences

- The official frontend and headless clients can build a complete typed table without draft/version privileges or hard-coded PostgreSQL type rules.
- Draft display edits become visible only after a successful publication, matching row data and schema activation atomically.
- The descriptor is derived and needs no new metadata table or migration.
- A newly active snapshot invalidates row cursors as before; clients can compare snapshot IDs and restart exploration consistently.
- Personal saved views, column widths, filter presets, distinct-value facets, and filtered total counts remain future capabilities.

## Alternatives considered

- **Expose the current draft to every row viewer:** rejected because it leaks authoring metadata and can disagree with active data.
- **Require a trial row query to discover columns:** rejected because the caller cannot safely construct that query without field UUIDs.
- **Duplicate display configuration in snapshot metadata:** rejected because the immutable published version already owns that configuration.
- **Parse semicolon-delimited filters in the backend:** rejected by ADR-0014 because it creates an ambiguous mini-language; clients emit explicit typed values instead.
