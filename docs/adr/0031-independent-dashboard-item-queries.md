# ADR-0031: Independent dashboard item queries

- Status: Accepted
- Date: 2026-10-13

## Context

A dashboard can contain up to 50 charts backed by unrelated datasets. Its frontend must show independent loading, success, stale-data, and failure states: one slow, unpublished, invalid, or temporarily unavailable chart must not delay or erase successful siblings. A dashboard-context route must also avoid becoming a way to submit an arbitrary chart identifier or analytical definition.

## Decision

- Expose `POST /api/v1/dashboards/{dashboard_id}/items/{item_id}/data:query` as one request per saved dashboard item.
- Resolve the chart identifier only from the current authoritative dashboard definition. The request accepts neither a chart ID nor a query definition.
- Keep this first execution slice private to the dashboard owner and administrators. Reuse the saved-chart query service so every call re-checks chart visibility, current dataset `view_rows`, active snapshot availability, definition compatibility, and DuckDB resource limits.
- Return the dashboard ID, dashboard revision, and item ID with the existing chart result and freshness metadata. A client may discard a late result when its dashboard revision no longer matches the displayed layout.
- Preserve stable safe HTTP failures per item. Clients request items independently and may cancel, retry, or render them without coordinating through a server-side bulk operation.

## Consequences

- Charts from different datasets load and fail independently without cross-dataset joins, result aggregation, or one slow request holding the complete dashboard response.
- Browser and API clients control parallelism and can render progressive results naturally. Existing server-side DuckDB concurrency and resource bounds still apply to every chart query.
- Query audit events continue to describe the saved chart, snapshot, actor, row count, and safe outcome through the existing chart path.
- Viewer dashboard grants and constrained viewer execution remain a later authorization slice. This endpoint must not be widened to viewers until the dashboard ACL and non-row-level aggregate permission model are accepted.

## Alternatives considered

- **One bulk endpoint returning every chart:** rejected because it couples latency and cancellation, requires partial-error envelopes, and makes a single HTTP request wait for the slowest tile.
- **Let clients call direct chart endpoints:** rejected as the only dashboard contract because it cannot prove that the requested chart is still present in the current layout and cannot later express constrained viewer access safely.
- **Accept chart IDs or arbitrary definitions in dashboard context:** rejected because callers could substitute resources outside the saved dashboard authorization boundary.
