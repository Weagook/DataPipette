# ADR-0014: Typed snapshot row queries

- Status: Accepted
- Date: 2026-10-10

## Context

Published Parquet snapshots need an interactive table API that can serve millions of rows without querying source PostgreSQL, accepting arbitrary SQL, or relying on offset pagination. Filters arrive from No-Code controls, but their values are untrusted. Sorting can contain duplicate and null values, and a snapshot may be atomically replaced between page requests.

## Decision

- Expose the read-only operation as `POST /api/v1/datasets/{dataset_id}/rows:query`. POST carries a bounded typed body but does not require CSRF because it does not mutate state.
- Require explicit field UUIDs. Filters and sorts resolve those UUIDs against the active published version; clients cannot supply SQL identifiers or expressions.
- Support equality, inequality, numeric/temporal comparisons, inclusive range, set membership, text contains/prefix, and null predicates. All values are normalized by the published PostgreSQL type and bound as DuckDB parameters.
- Treat semicolon-separated Excel-like input as a UI concern. The frontend converts it into an explicit `in` array; the backend never parses an ambiguous filter language.
- Query the immutable Parquet object with an isolated in-memory DuckDB connection. Limit threads, memory, temporary disk, execution time, selected columns, filters, sort keys, page rows, set values, and encoded response bytes.
- Use keyset cursors over explicit sort keys with `NULLS LAST`, followed by DuckDB's immutable-file `file_row_number` as the final ascending tie-breaker. Cursors are AES-GCM encrypted, authenticated, and bound to dataset ID, snapshot ID, and a digest of projection, filters, and sorting. A replaced snapshot or changed query invalidates the cursor.
- Store UUID-derived physical Parquet column names in snapshot schema version 2 while preserving logical names in metadata. This prevents a user field from colliding with DuckDB's technical `file_row_number` column. Schema version 1 remains readable when it has no such collision.
- Return logical typed rows, selected column metadata, active snapshot identity and activation time, source row count, current refresh state, stale/error metadata, byte truncation, and a nullable next cursor. Never calculate total filtered count in the interactive request.
- Audit only snapshot ID and bounded counts. Filter values, rows, SQL, cursors, and absolute storage paths are excluded.
- Keep the endpoint administrator-only until dataset ACLs exist. Role names alone are not a substitute for object-level grants.

## Consequences

- Interactive reads use Parquet predicate/projection pushdown and do not load complete snapshots into Python memory.
- Duplicate sort values paginate deterministically within one immutable snapshot.
- Cursor tokens can contain the last sort values without disclosing them to clients or logs.
- A cursor intentionally becomes unusable after refresh publication; the UI restarts from the first page and shows the new snapshot identity.
- Response byte truncation can produce fewer rows than requested while still returning a continuation cursor.
- DuckDB and `pytz` are runtime dependencies: DuckDB provides analytical Parquet execution, while its Python timestamp conversion requires `pytz` for timezone-aware values.
- Local filesystem reads remain a development/test adapter. S3 integration and concurrent production benchmarks remain future work.

## Alternatives considered

- Offset pagination was rejected because later pages require increasingly expensive scans and are not bound to a stable query position.
- Returning every matching row was rejected because it violates API memory and response limits.
- Accepting expressions such as `amount > 10` or parsing semicolon strings in the backend was rejected because it creates an ambiguous mini-language and a larger injection surface.
- Plain signed base64 cursors were rejected because sort positions can contain business values visible to the holder.
- PyArrow-only filtering was rejected because DuckDB provides a clearer parameterized SQL boundary for combined projection, predicates, ordering, and keyset pagination.
