# ADR-0030: Private versioned dashboard layouts

- Status: Accepted
- Date: 2026-10-12

## Context

DataPipette needs a durable backend contract for a drag-and-resize dashboard editor before the frontend is built. A dashboard may arrange charts backed by different datasets, but it must not become a second query language or imply a cross-dataset join. Concurrent browser tabs must not silently overwrite each other, and a saved layout must not retain references to missing or another owner's private charts.

## Decision

- Store each dashboard as a private owner-bound resource. Its complete schema-versioned definition is authoritative JSONB in metadata PostgreSQL; a normalized dependency row for every chart reference supports integrity checks and later lifecycle analysis.
- Use a fixed 12-column grid. Each item has its own stable UUID, a chart UUID, and integer `x`, `y`, `width`, and `height` geometry. Items must remain inside the bounded grid, must not overlap, and must reference a chart at most once. An empty dashboard is valid and a dashboard may contain at most 50 items.
- Permit charts from multiple datasets. The dashboard stores and arranges references only; it performs no cross-dataset join, calculation, or query. Chart result loading and failure isolation remain separate concerns.
- Require every referenced chart to exist and belong to the dashboard owner. Administrators may operate another owner's dashboard, but cannot attach charts owned by someone else to it.
- Expose create, cursor-list, read, and complete-replacement endpoints. Replacement requires the current opaque `"dashboard-N"` ETag and increments the revision only when the normalized name or definition changes.
- Keep dashboards visible only to their owner and administrators in this slice. Conceal unauthorized direct reads as not found, return no dashboards to viewers, require CSRF for writes, and audit successful material creation and replacement without storing chart results or business data.

## Consequences

- The future frontend can persist deterministic drag-and-resize state without inventing a separate client-only document format.
- A dashboard may safely compose charts from unrelated datasets while preserving their independent data and authorization boundaries.
- JSONB keeps the complete editable document easy to load, while normalized dependencies let the database prevent deletion of referenced charts and support future impact analysis.
- Shared/viewer dashboard-context execution, dashboard filters, responsive breakpoints, archival/deletion, and persisted per-chart runtime state are intentionally deferred.

## Alternatives considered

- **Store only normalized rows:** rejected because reconstructing an evolving editor document would make schema evolution and atomic replacement unnecessarily complex.
- **Store only JSONB:** rejected because chart lifecycle and dependency inspection should not require scanning arbitrary JSON documents.
- **Allow arbitrary coordinates or overlapping items:** rejected because every client would need its own conflict resolution and layout interpretation.
- **Copy chart definitions into dashboards:** rejected because copies drift, weaken chart ownership rules, and blur the boundary between layout and analytical query resources.
