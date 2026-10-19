# DatePipette reference benchmark

This directory contains the reproducible workload for the DatePipette MVP performance baseline. It measures one complete product path rather than an isolated SQL statement:

1. generate a deterministic PostgreSQL source fixture;
2. create and test a source connection through the public API;
3. validate, preview, refresh, and atomically publish a 50-column dataset;
4. query the resulting Parquet snapshot with a mixed interactive workload;
5. run bounded background exports through the ordinary worker path.

The accepted reference target is a 5-million-row, 50-column snapshot on a 4 CPU / 8 GB DatePipette node, 20 active interactive clients, and two admitted heavy background jobs. One environment cannot prove universal production capacity, so every published result must include hardware, storage topology, configuration, dataset shape, run duration, success/capacity responses, and known limitations.

The first measured baseline is published in [`REPORT_2026-10-19.md`](REPORT_2026-10-19.md). It records a failed 50-column acceptance run and a successful 20-column diagnostic run; keep that distinction explicit when comparing future results. The first refresh-path optimization and its unchanged-policy timeout are recorded separately in [`OPTIMIZATION_2026-10-20.md`](OPTIMIZATION_2026-10-20.md).

## Safety boundary

`sql/prepare_source.sql` drops and recreates only the dedicated `datepipette_benchmark` schema. Never rename that schema to an existing business schema. The API harness creates resources named `Reference PostgreSQL benchmark` and `Reference 5M x 50`; it never prints credentials and reads all secrets from process environment variables.

The fixture account needs `CONNECT`, `CREATE`, and `TEMP` on the benchmark database. DatePipette still opens source operations as read-only transactions. Use only synthetic data.

## 1. Generate the source fixture

The following evaluation-stack command reuses the PostgreSQL client already present in the metadata container. It copies no credentials and prompts for the source password interactively:

```bash
export BENCHMARK_SOURCE_HOST='private-postgresql-endpoint'
export BENCHMARK_SOURCE_USER='benchmark-user'
export BENCHMARK_SOURCE_DATABASE='benchmark-database'
docker cp benchmarks/sql/prepare_source.sql datepipette-metadata-db-1:/tmp/prepare_source.sql
docker exec -it datepipette-metadata-db-1 \
  psql -h "$BENCHMARK_SOURCE_HOST" -p 5432 \
  -U "$BENCHMARK_SOURCE_USER" -d "$BENCHMARK_SOURCE_DATABASE" \
  -v row_count=5000000 -v customer_count=100000 \
  -f /tmp/prepare_source.sql
```

The final queries must report exactly 5,000,000 events, zero invalid identifiers, and the source heap/index footprint. Preserve that output with the benchmark report.

For a cheap tooling check, override `row_count` with a smaller value. Re-running the command replaces only the dedicated benchmark schema.

## 2. Publish the DatePipette snapshot

Use environment variables so passwords do not appear in the command line or committed files:

```bash
export DATEPIPETTE_BENCHMARK_ADMIN_EMAIL='admin@example.com'
read -rsp 'DatePipette admin password: ' DATEPIPETTE_BENCHMARK_ADMIN_PASSWORD
export DATEPIPETTE_BENCHMARK_ADMIN_PASSWORD
read -rsp 'Benchmark PostgreSQL password: ' DATEPIPETTE_BENCHMARK_SOURCE_PASSWORD
export DATEPIPETTE_BENCHMARK_SOURCE_PASSWORD

python3 benchmarks/datepipette_benchmark.py setup \
  --output benchmarks/results/reference-setup.json
```

Use `--field-count 20` only for width-isolation diagnostics. Omitting it preserves the accepted 50-column workload.

The setup command is intentionally API-only. It tests the source connection, creates or reuses the named dataset, records Query Safety evidence, applies an explicit administrator override only if the accepted 5-million-row workload is blocked, previews 100 rows, requests a durable refresh, waits for its terminal state, and records worker metrics.

## 3. Run interactive workloads

Use the published dataset ID from `reference-setup.json`. Run the same warm-up and measured window at each concurrency level:

```bash
python3 benchmarks/datepipette_benchmark.py load \
  --dataset-id DATASET_ID --concurrency 1 --warmup 15 --duration 120 \
  --output benchmarks/results/interactive-c1.json

python3 benchmarks/datepipette_benchmark.py load \
  --dataset-id DATASET_ID --concurrency 5 --warmup 15 --duration 120 \
  --output benchmarks/results/interactive-c5.json

python3 benchmarks/datepipette_benchmark.py load \
  --dataset-id DATASET_ID --concurrency 10 --warmup 15 --duration 120 \
  --output benchmarks/results/interactive-c10.json

python3 benchmarks/datepipette_benchmark.py load \
  --dataset-id DATASET_ID --concurrency 20 --warmup 15 --duration 120 \
  --output benchmarks/results/interactive-c20.json
```

Each virtual client owns one persistent HTTP connection and continuously rotates through the public row-query shapes supported by the published snapshot:

- indexed point lookup;
- categorical filter plus timestamp sort;
- date and numeric range filters;
- text `contains` scan when the selected width includes its source field;
- a page projecting all selected benchmark fields.

The report separates successful latency from `503` admission responses. Capacity rejection is an expected governed outcome and must never be hidden in aggregate latency.

## 4. Run two bounded background jobs

The reference Compose environment must run a worker with concurrency two before this scenario can demonstrate simultaneous execution. The jobs each export a non-overlapping 250,000-row, 12-column CSV slice:

```bash
python3 benchmarks/datepipette_benchmark.py exports \
  --dataset-id DATASET_ID --jobs 2 --export-rows 250000 --export-fields 12 \
  --output benchmarks/results/concurrent-exports.json
```

Record both individual execution durations and wall-clock duration. A single-concurrency worker may still run the command, but that result demonstrates queueing rather than the accepted two-heavy-job target.

## Verification

The harness uses only the Python standard library. Run its deterministic tests from the repository root:

```bash
python3 -m unittest discover -s benchmarks/tests
```

Raw results must contain no passwords, session cookies, CSRF tokens, connection strings, customer data, or signed download links. Review every result before committing it.

## Teardown

Source cleanup is deliberately separate from normal runs and affects only the synthetic schema:

```bash
docker cp benchmarks/sql/drop_source.sql datepipette-metadata-db-1:/tmp/drop_source.sql
docker exec -it datepipette-metadata-db-1 \
  psql -h "$BENCHMARK_SOURCE_HOST" -p 5432 \
  -U "$BENCHMARK_SOURCE_USER" -d "$BENCHMARK_SOURCE_DATABASE" \
  -f /tmp/drop_source.sql
```

DatePipette benchmark resources remain visible for later inspection. Remove them only through supported product operations when lifecycle deletion is available.
