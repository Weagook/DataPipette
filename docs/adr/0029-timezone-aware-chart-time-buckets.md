# ADR-0029: Timezone-aware chart time buckets

- Status: Accepted
- Date: 2026-10-12

## Context

The initial chart query contract can group by an exact date or timestamp value, but useful line and bar charts need stable calendar buckets. A timestamp instant can belong to different hours, days, or months depending on timezone. Letting each chart provide an arbitrary timezone would make dashboards inconsistent and complicate caching, while truncating UTC values would produce surprising business-day boundaries for installations outside UTC.

## Decision

- Chart definition schema version 2 adds an optional `time_bucket` to its single dimension. Supported values are `hour`, `day`, `week`, `month`, `quarter`, and `year`.
- Continue reading and executing version-1 definitions unchanged. Version 1 rejects `time_bucket`; version 2 is the default for newly submitted definitions.
- Permit buckets only on PostgreSQL `date` and timestamp fields. `hour` requires a timestamp because a date has no smaller time component.
- Convert `timestamp with time zone` values to the validated installation IANA timezone before applying DuckDB `date_trunc`. Treat `timestamp without time zone` as already expressed in that timezone. Never timezone-convert `date` values.
- Use DuckDB's ISO-style week boundary, which starts on Monday. Bucket boundaries are returned as installation-local `timestamp without time zone` values; result-column metadata includes the selected bucket and the installation timezone for timestamp dimensions.
- Resolve the installation timezone from server configuration, never from chart input. Preserve the existing snapshot-only execution, typed filters, deterministic sorting, and resource bounds.

## Consequences

- Charts use one predictable business calendar across the installation and daylight-saving transitions are handled by the IANA timezone database.
- Existing saved charts remain valid without a data migration because definitions are complete JSONB documents and version 1 stays supported.
- A future installation-timezone change may alter bucket membership for the same immutable snapshot; changing that global setting therefore remains an administrator deployment decision.
- Fiscal calendars, custom week starts, per-dashboard timezones, and arbitrary date expressions remain outside this slice.

## Alternatives considered

- **Always bucket in UTC:** rejected because local day and month boundaries would be wrong for most business reporting outside UTC.
- **Store a timezone per chart:** rejected for MVP because the accepted product model uses one installation timezone.
- **Rewrite all stored definitions to version 2:** rejected because compatible readers are safer than an unnecessary metadata migration.
