# Snapshot refresh optimization report — 2026-10-29

This report measures the columnar CSV refresh stream ([ADR-0048](../docs/adr/0048-columnar-csv-refresh-stream.md)) against the existing row stream on the unchanged 5-million-row × 50-column reference relation. It isolates the refresh writer; it is not a complete API benchmark run and not a universal capacity claim.

## Verdict

With the source PostgreSQL on the same host as the worker, the product refresh writer produced the 5M × 50 snapshot in **42.8 seconds** through the CSV stream, against **312.1 seconds** through the existing row stream — 7.3 times faster with 43 % less peak memory. Both are far inside the default 1,800-second source deadline, which the 2026-10-19 and 2026-10-20 runs against a remote cluster exceeded.

The reference target also requires the 20-client interactive workload and two simultaneous heavy jobs; neither was re-measured here, so the overall target remains open.

## Environment

| Component | Configuration |
| --- | --- |
| DataPipette revision | `perf/csv-refresh-stream` at `c86e35b` |
| Node | 4 vCPU, 7.8 GiB RAM, 94 GiB disk; container limited to 4 CPUs and 6 GiB |
| Source PostgreSQL | 17.11 (`postgres:17-alpine`) in a container on the same Docker network as the worker; `shared_buffers=1GB` |
| Fixture | `benchmarks/sql/prepare_source.sql`, 5,000,000 events, 100,000 customers, 2,338 MB relation |
| Concurrent load | An existing DataPipette evaluation stack kept running on the node; its worker and MinIO used roughly 40–60 % of one CPU during the runs |

## Method

1. Generated the fixture with the documented script.
2. Isolated where time goes with three probes on 500,000 rows: a plain client `COPY` (`FORMAT BINARY`), the named binary cursor without conversion, and the cursor with Arrow conversion and Parquet writing.
3. Measured PostgreSQL's own encoding cost by running `COPY ... TO '/dev/null'` inside the source container in both formats and for column subsets.
4. Ran the product `ParquetSnapshotExecutor` write path (connection, read-only transaction, streaming, Parquet writer, fsync, row-count and schema validation, SHA-256, local publication) on all 5,000,000 rows, once through the CSV stream and once with the CSV stream forced to reject so that the row stream produced the snapshot.

## Findings

### Binary encoding of wide rows is the dominant source-side cost

| 500,000 rows, server-side `COPY` to `/dev/null` | Binary | Text |
| --- | ---: | ---: |
| All 50 benchmark columns | 20.3 s | 2.6 s |
| 32 non-text columns | 12.0 s | 2.4 s |
| First 16 non-text columns | 1.1 s | — |
| Last 16 non-text columns | 1.2 s | — |

The two 16-column halves encode quickly on their own, but together they take ten times longer in binary. The penalty depends on row width, not on a single slow type. Text output stays proportional.

### Per-cell Python objects dominate the client side

| 500,000 rows over the local Docker network | Duration |
| --- | ---: |
| Client binary `COPY`, bytes discarded | 22.4 s |
| Named binary cursor, rows discarded | 29.3 s |
| Named binary cursor + Arrow + Parquet | 35.4 s |
| CSV `COPY` + Arrow CSV parser + Parquet | 7.3 s |

### Product refresh writer, 5,000,000 rows × 50 columns

| Stream | Duration | Parquet size | Peak RSS |
| --- | ---: | ---: | ---: |
| Row stream (binary cursor) | 312.1 s | 228,530,204 bytes | 644 MiB |
| CSV stream | 42.8 s | 229,179,639 bytes | 368 MiB |

Both runs used the same image and were executed back to back. An earlier CSV run on the previous commit took 46.0 s with 351 MiB peak RSS. Parquet sizes differ slightly because batch boundaries, and therefore row-group and page boundaries, differ between the streams.

Snapshot contents are identical between the two streams; `backend/tests/test_parquet_csv_stream_integration.py` checks this for every CSV-compatible type under deliberately different session settings.

## Relation to the 2026-10-20 diagnosis

That report attributed the timeout to the PostgreSQL-to-application network path, based on slow remote `COPY` controls in both CSV and binary formats. The colocated measurements here remove the network and show that binary encoding and per-cell client work were each large enough on their own to dominate the refresh. The remote network may still add cost; it should be re-measured with the new stream before drawing a conclusion about it.

## Next measurements

1. Repeat the complete API benchmark (`setup`, interactive `load`, `exports`) with this revision against the same colocated source and against the remote cluster.
2. Address the interactive bottleneck documented on 2026-10-19: every S3-backed query still downloads and verifies the complete snapshot.
3. Run the two-heavy-job scenario with worker concurrency two.
