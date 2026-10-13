# ADR-0032: Dashboard sharing and constrained viewer execution

## Status

Accepted — 2026-10-13

## Context

Dashboards are useful to consumers who must see approved aggregate results without receiving the underlying dataset rows, export capability, source catalog, or a general chart authoring surface. Access must be assignable to one person or a reusable group, remain private by default, and disappear on the next request after a grant or membership is revoked.

## Decision

- Add one additive `view` capability represented by a dashboard grant to either an active user or an existing group. There are no deny grants, nested groups, or recipient edit rights.
- Dashboard owners and administrators may list, create, and revoke grants. Only owners and administrators may replace layouts or manage grants. Unauthorized resource reads are concealed as `404`.
- Active recipients may list and read shared dashboards and query a saved item. Effective access is evaluated from metadata PostgreSQL on every request, including current group membership.
- A dashboard-item request continues to accept only dashboard ID and item ID. After dashboard authorization, the backend resolves the chart from the authoritative saved layout and verifies that the chart still belongs to the dashboard owner.
- For a shared recipient, dashboard-context execution deliberately does not require or confer dataset `view_rows`. Owners and administrators retain the ordinary direct-chart authorization path. Both paths load only the active published snapshot, validate the immutable saved chart definition against the current field contract, bind filter values, apply DuckDB resource/response limits, and record the actual caller in the chart-query audit event.
- Direct chart, dataset row, export, source catalog, and arbitrary definition endpoints keep their existing authorization. A dashboard grant cannot be reused outside the saved-item route.

## Consequences

- A `Viewer` can consume curated aggregate results without gaining a path to detailed business rows.
- Revoking a direct grant or group membership affects the next request because dashboard authorization is not cached.
- The dashboard owner controls both layout and every referenced chart, avoiding mixed-owner privilege bridges.
- This model does not provide row-level security. If an aggregate chart itself exposes sensitive values or very small groups, the owner must not share that dashboard; minimum-group-size policy is a separate future capability.

## Alternatives considered

- **Reuse dataset `view_rows`:** rejected because it would give dashboard consumers substantially broader access than the curated aggregate result.
- **Copy result data into the dashboard:** rejected because it creates stale duplicated business data and bypasses active snapshot/freshness semantics.
- **Let viewers call direct chart queries:** rejected because that surface cannot prove the chart remains part of an authorized saved layout and could later enable chart discovery or substitution.
