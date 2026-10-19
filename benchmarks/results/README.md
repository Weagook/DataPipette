# Benchmark results

This directory holds reviewed machine-readable output from the reproducible reference workload. Every committed run must be accompanied by a Markdown report that identifies the DatePipette revision, hardware, PostgreSQL topology and version, deployment settings, exact commands, source and snapshot sizes, and any environmental limitations.

Never commit credentials, cookies, tokens, signed download links, real customer data, or unreviewed diagnostic logs.

The first measured baseline is documented in [`../REPORT_2026-10-19.md`](../REPORT_2026-10-19.md). The `reference-setup-20.json` result is a width-isolation run; it must not be presented as satisfying the accepted 50-column target.
