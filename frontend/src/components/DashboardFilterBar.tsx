import { useQuery } from "@tanstack/react-query";
import { CalendarRange, ChevronDown, ListFilter, Search, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { listDashboardFilterValues, problemMessage } from "../lib/api";
import { isActive, type DashboardFilter, type FilterSelection, type FilterValue } from "../lib/dashboardFilters";
import { useI18n } from "../lib/i18n";
import { queryKeys } from "../lib/query";
import "./DashboardFilters.css";

type Props = {
  dashboardId: string;
  filters: DashboardFilter[];
  selections: FilterSelection[];
  onChange: (selections: FilterSelection[]) => void;
};

export function DashboardFilterBar({ dashboardId, filters, selections, onChange }: Props) {
  const { t } = useI18n();
  const selectionOf = (filterId: string) => selections.find((item) => item.filter_id === filterId) ?? { filter_id: filterId };
  const replace = (selection: FilterSelection) =>
    onChange([...selections.filter((item) => item.filter_id !== selection.filter_id), selection].filter(isActive));

  return (
    <section className="dashboard-filters" aria-label={t("Dashboard filters")}>
      {filters.map((filter) =>
        filter.kind === "values" ? (
          <ValueFilter key={filter.id} dashboardId={dashboardId} filter={filter} selection={selectionOf(filter.id)} onChange={replace} />
        ) : (
          <DateFilter key={filter.id} filter={filter} selection={selectionOf(filter.id)} onChange={replace} />
        ),
      )}
      {selections.some(isActive) && (
        <button className="button secondary small" type="button" onClick={() => onChange([])}>
          <X size={14} /> {t("Clear filters")}
        </button>
      )}
    </section>
  );
}

function ValueFilter({
  dashboardId,
  filter,
  selection,
  onChange,
}: {
  dashboardId: string;
  filter: DashboardFilter;
  selection: FilterSelection;
  onChange: (selection: FilterSelection) => void;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const root = useRef<HTMLDetailsElement>(null);
  const options = useQuery({
    queryKey: queryKeys.dashboardFilterValues(dashboardId, filter.id),
    queryFn: () => listDashboardFilterValues(dashboardId, filter.id),
    enabled: open,
    staleTime: 60_000,
  });
  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);

  const chosen = selection.values ?? [];
  const label = (value: FilterValue) => (typeof value === "boolean" ? t(value ? "Yes" : "No") : String(value));
  const summary = chosen.length === 0 ? t("All") : chosen.length <= 2 ? chosen.map(label).join(", ") : t("{count} selected", { count: chosen.length });
  const needle = search.trim().toLocaleLowerCase();
  const visible = (options.data?.values ?? []).filter((value) => label(value).toLocaleLowerCase().includes(needle));
  const toggle = (value: FilterValue) =>
    onChange({ filter_id: filter.id, values: chosen.includes(value) ? chosen.filter((item) => item !== value) : [...chosen, value] });

  return (
    <details className={`filter-chip${chosen.length ? " active" : ""}`} ref={root} open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        <ListFilter size={15} />
        <span>{filter.label}:</span>
        <strong>{summary}</strong>
        <ChevronDown size={15} />
      </summary>
      <div className="filter-popover" role="group" aria-label={filter.label}>
        {(options.data?.values.length ?? 0) > 8 && (
          <label className="filter-search">
            <Search size={15} />
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t("Search values")} aria-label={t("Search {name}", { name: filter.label })} autoFocus />
          </label>
        )}
        {options.isPending && <div className="filter-options-skeleton skeleton" />}
        {options.isError && <p className="filter-note error">{problemMessage(options.error)}</p>}
        <div className="filter-options">
          {visible.map((value) => (
            <label key={String(value)}>
              <input type="checkbox" checked={chosen.includes(value)} onChange={() => toggle(value)} />
              <span>{label(value)}</span>
            </label>
          ))}
          {options.data && visible.length === 0 && <p className="filter-note">{t("No matching values")}</p>}
        </div>
        {options.data?.truncated && <p className="filter-note">{t("Only the first 200 values are listed.")}</p>}
        {chosen.length > 0 && (
          <button className="text-link" type="button" onClick={() => onChange({ filter_id: filter.id })}>
            {t("Show all")}
          </button>
        )}
      </div>
    </details>
  );
}

function DateFilter({
  filter,
  selection,
  onChange,
}: {
  filter: DashboardFilter;
  selection: FilterSelection;
  onChange: (selection: FilterSelection) => void;
}) {
  const { t } = useI18n();
  const from = selection.from ?? "";
  const to = selection.to ?? "";
  const change = (next: { from?: string; to?: string }) => {
    const value = { from, to, ...next };
    onChange({ filter_id: filter.id, from: value.from || null, to: value.to || null });
  };
  return (
    <div className={`filter-chip date${from || to ? " active" : ""}`} role="group" aria-label={filter.label}>
      <CalendarRange size={15} />
      <span>{filter.label}:</span>
      <input type="date" value={from} max={to || undefined} onChange={(event) => change({ from: event.target.value })} aria-label={t("{name} from", { name: filter.label })} />
      <span aria-hidden="true">–</span>
      <input type="date" value={to} min={from || undefined} onChange={(event) => change({ to: event.target.value })} aria-label={t("{name} to", { name: filter.label })} />
    </div>
  );
}
