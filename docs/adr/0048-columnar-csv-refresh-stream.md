# ADR-0048: Columnar CSV refresh stream

- Status: Accepted
- Date: 2026-10-29

## Context

ADR-0013 fetches refresh rows through a named server-side cursor and converts each batch into Arrow. The 2026-10-20 optimization switched that cursor to PostgreSQL's binary result format and vectorized the Arrow conversion, yet the reference 5-million-row × 50-column refresh still exceeded the 30-minute source deadline. That report attributed the remaining time to the network path because a remote `COPY` was also slow.

Measurements against a PostgreSQL 17 source on the same host as the worker, with no network in between, isolated two independent costs:

- PostgreSQL's binary output for the wide benchmark row was about five times slower than its text output on the server itself: 20.3 s against 2.6 s for 500,000 rows written by `COPY` to `/dev/null`. Narrower column subsets encoded quickly in both formats, so the penalty grows with row width rather than with any single type.
- Even after the transfer, the driver materialized one Python object per cell. Fetching 5M × 50 rows took 296 s before conversion, and Arrow conversion added 68 s.

## Decision

- When every result column has a type whose PostgreSQL text form Arrow parses into the identical stored value, the refresh worker runs `COPY (<compiled select>) TO STDOUT (FORMAT csv)` inside the same read-only, deadline-bound source transaction and streams the bytes into Arrow's multithreaded CSV reader with an explicit column schema. Parsed batches are written to the same Parquet writer with the same schema, statistics, checksums, row-limit check, and non-null enforcement.
- CSV-compatible types are booleans, integer and floating types, `numeric(p,s)` with precision up to 38 and non-negative scale, text and character types, dates, timestamps with or without time zone, and UUIDs. All other supported types keep the existing row stream, unchanged.
- The stream pins `TimeZone=UTC`, `DateStyle=ISO, YMD`, and `extra_float_digits=3` for its transaction, so role or database defaults cannot change how values are rendered. Unquoted empty fields are NULL; quoted empty strings stay empty strings.
- If Arrow rejects the CSV stream (for example a value it cannot parse), the partial staging file is discarded and the refresh is repeated through the row stream in a new source transaction. A dataset therefore never fails only because of the fast path.
- Source bytes reach the parser through a bounded in-memory queue of 4 MiB chunks, so memory stays bounded by the queue and Arrow's 16 MiB parse blocks rather than by the result size. Cancellation still cancels the source query, and a consumer failure stops the producer.

## Consequences

- On a 4 vCPU / 8 GiB node with a colocated PostgreSQL 17 source, the product refresh writer builds the reference 5M × 50 snapshot in 42.8 seconds instead of 312.1 seconds, with 368 MiB instead of 644 MiB peak memory and an equivalent Parquet snapshot of about 229 MB. See [the measurement report](../../benchmarks/OPTIMIZATION_2026-10-29.md).
- A refresh may read the source twice when the fast path is rejected; the second pass is bounded by the same statement timeout.
- Snapshot contents are identical between the two streams for every CSV-compatible type, which an integration test checks under deliberately different session settings.
- Arrow's CSV decimal parser does not support 256-bit decimals, so `numeric` columns with precision above 38 always use the row stream.

## Alternatives considered

- **Keep binary results and blame the network:** rejected because the colocated measurement removed the network and the binary path stayed slow.
- **Text-format server-side cursor through Psycopg:** rejected because it removes the binary encoding penalty but keeps one Python object per cell.
- **DuckDB's PostgreSQL scanner:** rejected for now because it downloads a runtime extension, adds another connection and security surface, and does not apply the existing read-only transaction and deadline as directly.
- **ADBC PostgreSQL driver:** previously evaluated in the 2026-10-20 optimization without a gain, and it adds a native dependency; it uses binary `COPY`, which these measurements show is the slower encoding for wide rows.
