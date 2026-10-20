# Benchmark results

This directory holds reviewed machine-readable output from the reproducible reference workload. Every committed run must be accompanied by a Markdown report that identifies the DataPipette revision, hardware, PostgreSQL topology and version, deployment settings, exact commands, source and snapshot sizes, and any environmental limitations.

Never commit credentials, cookies, tokens, signed download links, real customer data, or unreviewed diagnostic logs.

The first measured baseline is documented in [`../REPORT_2026-10-19.md`](../REPORT_2026-10-19.md). The `reference-setup-20.json` result is a width-isolation run; it must not be presented as satisfying the accepted 50-column target.

The first refresh optimization and its unchanged-policy timeout are documented in [`../OPTIMIZATION_2026-10-20.md`](../OPTIMIZATION_2026-10-20.md), with the reviewed result in `reference-setup-50-vectorized-timeout.json`. A timed-out run is evidence about the measured environment, not a successful capacity result.
