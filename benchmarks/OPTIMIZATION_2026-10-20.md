# Snapshot refresh optimization report — 2026-10-20

This report evaluates the first refresh-path optimization against the unchanged 5-million-row × 50-column reference workload. It records a negative acceptance result as well as the bottleneck isolation. It is not a universal capacity claim.

## Verdict

The optimized implementation preserves the refresh safety contract, but the 5M × 50 target still does **not** complete within the default 1,800-second source deadline on this environment.

- PostgreSQL values are requested in binary form.
- Arrow batches are constructed column-wise rather than through a Python callback for every native cell.
- Fetching the next source batch overlaps Parquet conversion and writing of the current batch.
- High-cardinality Parquet dictionary encoding is disabled.
- All three accepted-workload attempts ended at the source deadline, published no snapshot, and removed their staging files.

The experiment therefore does not justify a claim that DatePipette now satisfies the accepted refresh target. It does show that further Parquet micro-optimization is not the next evidence-led step on this particular test path.

## Compared revisions

| Run | Revision | Result |
| --- | --- | --- |
| Initial reference | `3882587` | First 5M × 50 attempt timed out after 1,811.582 s |
| Vectorized refresh | `22f4123` | Three attempts timed out after 1,811.556 s, 1,808.232 s, and 1,811.538 s |

The timeout, row limit, workload, source relation, and application node were not relaxed between runs.

## What changed

The refresh worker now:

1. uses a binary server-side PostgreSQL cursor;
2. transposes each bounded row batch once and lets Arrow build typed columns;
3. fetches the next PostgreSQL batch while a worker thread converts and writes the current batch;
4. writes Zstandard-compressed Parquet without dictionary encoding;
5. retains cancellation, read-only source transactions, row limits, schema verification, checksums, atomic publication, and staging cleanup.

ADBC-based streaming and alternative Parquet compression settings were also evaluated during development and discarded because they did not improve the complete workload on this environment.

## Accepted-workload result

| Metric | Result |
| --- | ---: |
| Source rows | 5,000,000 |
| Selected fields | 50 |
| Attempts | 3 |
| Attempt durations | 1,811.556 s; 1,808.232 s; 1,811.538 s |
| Terminal state | `failed` |
| Error code | `source_unavailable` |
| Snapshot published | No |
| Staging artifacts retained | None |

The worker's task duration includes governed cancellation and cleanup around the 1,800-second PostgreSQL statement deadline.

## Bottleneck isolation

Two direct controls were run from the application node against the primary PostgreSQL node, outside DatePipette's Arrow and Parquet code.

First, `EXPLAIN (ANALYZE, BUFFERS)` over the first 500,000 wide rows completed inside PostgreSQL in 177.505 ms. The sequential scan itself is not the dominant elapsed cost.

Second, client-side `COPY` of the same 500,000-row, 50-column projection to `/dev/null` did not complete in either representation:

| Direct control | Observed lower bound | Result |
| --- | ---: | --- |
| CSV `COPY` | 279.010 s | Cancelled after establishing the bound |
| Binary `COPY` | >300 s | Cancelled after establishing the bound |

No Arrow conversion, Parquet compression, object storage upload, or DatePipette API execution was present in those controls. The evidence points to result serialization/transport/client consumption along the PostgreSQL-to-application path, rather than table scanning or Parquet encoding alone. This is a diagnosis for this environment, not proof of a universal PostgreSQL limitation.

The primary and replica are reachable directly at their private addresses. The configured endpoint used by the benchmark resolved consistently to the primary during five control connections, so replica rotation was not responsible for the three timeouts.

## Decision

The implementation is retained because it removes avoidable per-cell Python work while preserving bounded memory and safety semantics. The accepted performance target remains open.

The next meaningful measurement should be performed only after one of these environmental variables changes:

1. place the worker and PostgreSQL on a verified higher-throughput network path;
2. measure the private link independently with infrastructure-level network telemetry;
3. provide a PostgreSQL-side export mechanism or colocated extraction worker with an explicit trust and deployment model;
4. repeat the exact 5M × 50 workload on the dedicated benchmark server planned for capacity testing.

Raising the statement timeout would only hide the failed target and is deliberately not recommended as the acceptance fix.

## Reproduction artifact

The reviewed machine-readable result is [`results/reference-setup-50-vectorized-timeout.json`](results/reference-setup-50-vectorized-timeout.json). It excludes credentials, connection strings, cookies, signed URLs, and source data.
