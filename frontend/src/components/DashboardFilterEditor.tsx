import { useQueries } from "@tanstack/react-query";
import { SlidersHorizontal, Trash2 } from "lucide-react";

import { getDatasetExploration, type Chart, type DashboardDefinition } from "../lib/api";
import { filterKindFor, MAX_DASHBOARD_FILTERS, type DashboardFilter, type DashboardFilterKind } from "../lib/dashboardFilters";
import { itemsOf } from "../lib/dashboardLayout";
import { randomUuid } from "../lib/id";
import { useI18n } from "../lib/i18n";
import { queryKeys } from "../lib/query";
import "./DashboardFilters.css";

type Candidate = {
  key: string;
  name: string;
  label: string;
  kind: DashboardFilterKind;
  targets: DashboardFilter["targets"];
};

/**
 * Lets an author turn a column shared by the dashboard's charts into a viewer filter.
 * Columns with the same name and a compatible type are matched across datasets.
 */
export function DashboardFilterEditor({
  definition,
  chartById,
  onChange,
}: {
  definition: DashboardDefinition;
  chartById: Map<string, Chart>;
  onChange: (definition: DashboardDefinition) => void;
}) {
  const { t } = useI18n();
  const items = itemsOf(definition);
  const filters = definition.filters ?? [];
  const datasetIds = [...new Set(items.flatMap((item) => chartById.get(item.chart_id)?.dataset_id ?? []))];
  const explorations = useQueries({
    queries: datasetIds.map((id) => ({
      queryKey: queryKeys.datasetExploration(id),
      queryFn: () => getDatasetExploration(id),
      staleTime: 60_000,
    })),
  });
  const columnsByDataset = new Map(explorations.flatMap((query) => (query.data ? [[query.data.dataset_id, query.data.columns] as const] : [])));

  const candidates = new Map<string, Candidate>();
  for (const item of items) {
    const columns = columnsByDataset.get(chartById.get(item.chart_id)?.dataset_id ?? "") ?? [];
    for (const column of columns) {
      const kind = filterKindFor(column.data_type);
      if (!kind || !column.visible) continue;
      const key = `${kind}:${column.name}`;
      const candidate = candidates.get(key) ?? { key, name: column.name, label: column.label, kind, targets: [] };
      candidate.targets.push({ item_id: item.id, field_id: column.field_id });
      candidates.set(key, candidate);
    }
  }
  const keyOf = (filter: DashboardFilter) =>
    [...candidates.values()].find((candidate) => candidate.kind === filter.kind && candidate.targets.some((target) => filter.targets.some((used) => used.field_id === target.field_id)))?.key;
  const usedKeys = new Set(filters.map(keyOf));
  const available = [...candidates.values()].filter((candidate) => !usedKeys.has(candidate.key)).sort((a, b) => a.label.localeCompare(b.label));
  const sameLabel = (candidate: Candidate) => [...candidates.values()].filter((other) => other.label === candidate.label).length > 1;
  const save = (next: DashboardFilter[]) => onChange({ ...definition, schema_version: 2, filters: next });
  const update = (id: string, change: Partial<DashboardFilter>) => save(filters.map((filter) => (filter.id === id ? { ...filter, ...change } : filter)));
  const chartName = (itemId: string) => chartById.get(items.find((item) => item.id === itemId)?.chart_id ?? "")?.name ?? t("Unavailable chart");

  return (
    <section className="filter-editor" aria-label={t("Dashboard filters")}>
      <header>
        <span>
          <strong><SlidersHorizontal size={16} /> {t("Filters for viewers")}</strong>
          <small>{t("Viewers pick values and every linked chart updates. Columns with the same name are linked automatically.")}</small>
        </span>
        <select
          aria-label={t("Add a filter")}
          value=""
          disabled={filters.length >= MAX_DASHBOARD_FILTERS || available.length === 0}
          onChange={(event) => {
            const candidate = candidates.get(event.target.value);
            if (candidate) save([...filters, { id: randomUuid(), label: candidate.label, kind: candidate.kind, targets: candidate.targets }]);
          }}
        >
          <option value="">{t(filters.length >= MAX_DASHBOARD_FILTERS ? "At most 5 filters" : "Add a filter…")}</option>
          {available.map((candidate) => (
            <option key={candidate.key} value={candidate.key}>
              {candidate.label}{sameLabel(candidate) ? ` (${candidate.name})` : ""} · {t(candidate.kind === "date_range" ? "dates" : "list")} · {t("{count} charts", { count: candidate.targets.length })}
            </option>
          ))}
        </select>
      </header>
      {filters.length === 0 && (
        <p className="filter-editor-empty">{t(items.length ? "No filters yet. Pick a column to let viewers narrow every chart at once." : "Add charts first, then choose a column to filter by.")}</p>
      )}
      {filters.map((filter) => {
        const candidate = candidates.get(keyOf(filter) ?? "");
        const options = candidate?.targets ?? filter.targets;
        return (
          <div className="filter-editor-row" key={filter.id}>
            <div>
              <input type="text" value={filter.label} maxLength={60} onChange={(event) => update(filter.id, { label: event.target.value })} aria-label={t("Filter name")} />
              <span className="kind">{t(filter.kind === "date_range" ? "Date range" : "List of values")}</span>
              <button className="icon-button" type="button" onClick={() => save(filters.filter((item) => item.id !== filter.id))} aria-label={t("Remove filter {name}", { name: filter.label })}>
                <Trash2 size={15} />
              </button>
            </div>
            <div className="filter-targets" role="group" aria-label={t("Charts this filter changes")}>
              {options.map((option) => {
                const checked = filter.targets.some((target) => target.item_id === option.item_id);
                return (
                  <label key={option.item_id}>
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={checked && filter.targets.length === 1}
                      onChange={() =>
                        update(filter.id, {
                          targets: checked ? filter.targets.filter((target) => target.item_id !== option.item_id) : [...filter.targets, option],
                        })
                      }
                    />
                    {chartName(option.item_id)}
                  </label>
                );
              })}
            </div>
          </div>
        );
      })}
    </section>
  );
}
