# ADR-0052: Dashboard filters mapped to chart fields

- Status: Accepted
- Date: 2026-10-29

## Context

ADR-0030 deferred dashboard filters, and MVP listed dashboard-wide filters as out of scope. In practice a dashboard was a fixed picture: a viewer could not narrow it to one region, one product category, or one month without asking the author for another chart. Non-technical readers need to do this themselves.

A dashboard may combine charts from unrelated datasets (ADR-0030), each item executes independently (ADR-0031), and shared recipients receive only aggregated results through the constrained dashboard context (ADR-0032). A filter must therefore not introduce a cross-dataset join, must not let a caller supply an arbitrary field or chart definition, and must not widen what a viewer can read.

## Decision

- Dashboard definition schema version 2 adds up to five filters. Each filter has a stable UUID, a label of 1–60 characters, a kind, and explicit targets. A target is an `(item_id, field_id)` pair; each item can be targeted at most once per filter, and every target must reference an item in the same layout. Version 1 documents remain readable and behave as having no filters.
- Two kinds exist. `values` filters apply an `in` restriction and accept text, integer, and boolean fields. `date_range` filters apply an inclusive day range and accept date and timestamp fields; for timestamps the bounds are the start of the first day and the start of the day after the last day in the installation timezone, matching chart time buckets.
- Saving validates every target against the active published contract of the item's chart dataset. A chart whose dataset has no loaded snapshot cannot be targeted.
- The item query route accepts an optional body of selections (`filter_id` plus `values`, or `from`/`to`). The server looks up the filter in the current saved definition, ignores filters that do not target the requested item, and appends the resulting typed, bound-parameter restrictions to the chart's saved filters. The chart definition itself is unchanged and is not re-saved. A target field that disappeared from the active contract fails the item as `dashboard_item_invalid`, like other contract drift.
- `GET /dashboards/{id}/filters/{filter_id}/values` lists up to 200 sorted distinct values for a `values` filter across its targets, with the same authorization as reading the dashboard. It reuses the bounded chart executor with a one-dimension count query, so the interactive runtime policy and admission apply.
- The official frontend keeps the viewer's choices in the page address so a filtered view can be bookmarked or shared. The editor proposes filters from the columns of the dashboard's charts and links columns with the same name and a compatible type across datasets automatically; the author can untick charts.

## Consequences

- One choice narrows charts from several datasets, while each chart still queries only its own snapshot. There is still no cross-dataset join.
- An author who maps a field exposes, to everyone who can read the dashboard, that field's distinct values and the aggregates restricted to any chosen value. Mapping is an explicit author decision, recipients still cannot browse rows, export, or choose fields the author did not map, and minimum group sizes remain future work (ADR-0032).
- Value lists are not audited separately; item queries with selections are audited through the existing chart query events.
- Charts not targeted by an active filter keep showing unfiltered results. Cross-filtering by clicking a chart, relative date presets, and per-viewer saved filter state are future work.

## Alternatives considered

- **Match fields by name at query time:** rejected because a renamed or newly added column would silently change which charts a saved filter affects. Explicit targets are validated when saved; the editor still uses names to propose them.
- **Store selections in the dashboard definition:** rejected because the document belongs to the author, and viewers cannot write it.
- **Let clients send field identifiers with the query:** rejected because a recipient could then filter by any field of the dataset instead of only the ones the author exposed.
