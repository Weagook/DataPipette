# ADR-0051: Live preview of unsaved chart definitions

- Status: Accepted
- Date: 2026-10-29

## Context

ADR-0028 executed only saved chart definitions. Chart Studio therefore asked authors to save before they could see a chart, and every adjustment of a grouping, aggregation, or filter required another saved revision. Authors who are not data specialists could not explore what a chart would look like, and saved revisions accumulated for trial and error.

An author who may create a chart for a dataset already holds effective `view_rows` on that dataset and can browse its detailed rows. An aggregate of those rows reveals nothing additional.

## Decision

- Add `POST /api/v1/charts/data:preview` with `dataset_id` and a complete chart definition. The definition is validated with the same rules as creation and replacement and executed against the dataset's active snapshot through the existing bounded chart executor.
- Require an active Admin or Analyst browser session and effective dataset `view_rows`, exactly as creation does. Viewers, dashboard recipients, and bearer tokens cannot call it.
- Use the current managed interactive-query policy and installation-wide admission, so previews share the same time, memory, response-size, and concurrency limits as saved charts.
- Persist nothing except an audit event on the dataset: `chart.previewed` or `chart.preview_failed`, with snapshot, policy revision, returned rows, truncation, and safe error code.
- Chart Studio debounces edits and keeps the previous preview visible while the next one runs.

## Consequences

- Authors see the chart change as they edit and save only when they are satisfied.
- Interactive capacity is consumed by previews as well as saved charts. Debouncing and admission keep this bounded, and capacity responses are the same `503` with `Retry-After`.
- Audit volume grows with editing activity.
- Dashboard-item and saved-chart routes are unchanged; viewers still execute only saved definitions referenced by a shared dashboard.

## Alternatives considered

- **Auto-save every edit as a revision:** rejected because it would flood revision history and could break dashboards that reference the chart mid-edit.
- **Aggregate in the browser from row pages:** rejected because results would be wrong for snapshots larger than one page and would transfer detailed rows unnecessarily.
- **Allow previews for any role with dashboard access:** rejected because it would let viewers aggregate arbitrary fields of datasets they cannot browse.
