# Changelog

All notable changes to DataPipette are recorded here. Versions follow [Semantic Versioning](https://semver.org/). Before 1.0, a minor version may change the public API or require manual upgrade steps; the release notes say so.

## [0.1.0] - 2026-10-30

First published release: a complete single-company BI installation for PostgreSQL that runs on one server with automatic HTTPS.

### Data

- Encrypted PostgreSQL connections with safe tests, live catalog inspection, and exact visibility scopes per person or group.
- Datasets built on a drag-and-drop board of tables and links, step by step, or as read-only SQL. Validation, Query Safety warnings and policies, bounded preview, versioned drafts, and impact analysis on charts.
- Durable, cancellable refreshes into immutable Parquet snapshots on S3-compatible storage, with interval or daily schedules, retries, and recovery. A failed load keeps the previous snapshot readable.
- Snapshot tables with typed filters, sorting, cursor pagination, and CSV/XLSX exports through short-lived download links.

### Analysis

- Charts: bar, line, pie, donut, and KPI, with aggregations, filters, sorting, timezone-aware time buckets, and a live preview of unsaved changes.
- Dashboards with drag-and-resize layout, charts from several datasets, sharing with people and groups, and author-mapped list and date filters for viewers.

### Access and integration

- Local accounts with administrator, analyst, and viewer roles, groups, dataset and dashboard grants, and an administrator audit log.
- Service accounts with scoped API tokens, an OpenAPI contract, and a generated TypeScript client.
- English and Russian interface with a per-person language preference.

### Operations

- One-command Docker Compose installation and a single-server production overlay with automatic HTTPS (Caddy), closed internal ports, generated secrets, and production settings.
- Backup, verification, and restore of metadata and objects; connection encryption-key rotation; host health checks with Telegram or webhook alerts.
- Installation-wide admission for interactive queries and background jobs; configurable API processes.
- Published images for `linux/amd64` and `linux/arm64`: `ghcr.io/weagook/datapipette-backend` and `ghcr.io/weagook/datapipette-frontend`.

### Known limitations

- Backups stay on the server unless you copy them elsewhere; there is no point-in-time recovery.
- Only sign-in is rate-limited; other load is bounded by query admission.
- No metrics dashboards, SSO, high availability, or Kubernetes packaging.
- Measured capacity: about 80 people working with small and medium dashboards at the same time on 4 CPUs and 8 GB ([report](benchmarks/CAPACITY_2026-10-30.md)).
