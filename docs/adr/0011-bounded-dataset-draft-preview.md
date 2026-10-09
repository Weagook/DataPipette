# ADR-0011: Bounded synchronous dataset draft preview

- Status: Accepted
- Date: 2026-10-09

## Context

After validating a visual definition, an analyst needs to inspect a few real rows before publishing it. Preview crosses the business-data boundary and runs against the source PostgreSQL, so an apparently small API response must not permit an unbounded source result, client-memory allocation, response payload, or stale-definition race. Preview is exploratory: without an explicit sort, SQL does not promise which subset of rows a limit returns.

## Decision

- Expose preview as an explicit synchronous operation on the draft. Require an active administrator, CSRF, the current draft ETag, and a validation artifact for the same revision in `valid` or `valid_with_warnings` state.
- Recompile the structured visual definition for every preview with the shared identifier-safe compiler. Never execute `compiled_sql` stored in the validation artifact.
- Add a parameter-bound PostgreSQL `LIMIT` of requested rows plus one look-ahead row. Accept 1–200 requested rows and default to 100.
- Execute inside the existing read-only, statement-time-limited source transaction through a named Psycopg server-side cursor. Fetch small batches rather than buffering an unrestricted result in the API process.
- Apply a one-MiB budget to compact UTF-8 JSON encoding of row objects. Stop before adding a row that exceeds the remaining budget.
- Return explicit `row_limit` or `byte_limit` truncation metadata. An empty row list with `byte_limit` means the first source row exceeded the response budget; it is not an empty dataset.
- Normalize preview values deterministically: preserve native JSON primitives and structures, render arbitrary-precision decimals as strings, temporal values as ISO text, UUID/interval/other adapted values as text, and binary values as base64.
- Treat preview order as unspecified until typed sorting exists. Do not add offset pagination to live preview.
- After source execution, lock and compare the draft revision before auditing and returning rows. Discard rows and return a revision conflict if an edit won the race.
- Audit successful previews using only revision, requested/returned counts, and truncation reason. Audit source failures with a stable code; never record SQL, rows, values, credentials, or raw driver errors.

## Consequences

- The editor gets useful real-data feedback without creating Parquet or changing the active snapshot.
- A warning remains advisory: bounded preview is allowed, while future publication policy may still require override or block execution.
- The application holds only bounded batches plus the bounded response rows, although one unusually large cell must arrive from PostgreSQL before its encoded size can be measured.
- A row limit controls rows returned, not deterministic selection or every possible amount of work inside a future complex query. Query Safety Advisor, statement timeout, source privileges, and later complexity policies remain necessary.
- Preview is not a pagination API and cannot be used as evidence of complete data.

## Alternatives considered

- Executing stored generated SQL was rejected because metadata tampering would bypass the structured compiler trust boundary.
- A normal client-side cursor was rejected because it buffers the complete PostgreSQL result sent to the client; server-side cursors make partial retrieval explicit.
- Returning decimals as JSON numbers was rejected because clients can silently lose precision.
- Adding implicit primary-key ordering was rejected because not every source has a visible key and ordering can add substantial work. Explicit typed sorting belongs to a later query contract.
- Creating a temporary snapshot for every preview was rejected because it adds background-job and storage latency before the user has chosen to publish.
