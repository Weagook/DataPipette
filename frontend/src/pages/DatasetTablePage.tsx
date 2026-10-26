import { useQuery } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  ArrowUpDown,
  Check,
  ChevronLeft,
  ChevronRight,
  Columns3,
  DatabaseZap,
  RefreshCw,
  Rows3,
  TriangleAlert,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Link, Navigate, useOutletContext, useParams } from "react-router-dom";

import { DatasetFilterBar } from "../components/DatasetFilterBar";
import { ExportPanel } from "../components/ExportPanel";
import { InlineError } from "../components/Status";
import {
  getDatasetExploration,
  problemMessage,
  queryDatasetRows,
  type CurrentUser,
  type DatasetColumn,
  type DatasetExploration,
  type SnapshotFilter,
  type SnapshotRowsQuery,
  type SnapshotSort,
} from "../lib/api";
import { buildSnapshotFilters, formatDatasetValue, operatorLabels, type FilterDraft } from "../lib/datasetQuery";
import { formatDateTime } from "../lib/format";
import { useI18n } from "../lib/i18n";
import { queryKeys } from "../lib/query";

export function DatasetTablePage() {
  const { t } = useI18n();
  const user = useOutletContext<CurrentUser>();
  const { datasetId = "" } = useParams();
  const exploration = useQuery({
    queryKey: queryKeys.datasetExploration(datasetId),
    queryFn: () => getDatasetExploration(datasetId),
    enabled: datasetId !== "" && user.role !== "viewer",
  });

  if (user.role === "viewer") return <Navigate to="/overview" replace />;
  if (exploration.isPending) {
    return <div className="dataset-table-skeleton skeleton" aria-label={t("Loading dataset")} />;
  }
  if (exploration.isError) {
    return <InlineError message={problemMessage(exploration.error)} onRetry={() => void exploration.refetch()} />;
  }
  return <DatasetWorkspace key={exploration.data.snapshot_id} exploration={exploration.data} />;
}

function DatasetWorkspace({ exploration }: { exploration: DatasetExploration }) {
  const { locale, t } = useI18n();
  const visibleColumns = useMemo(
    () => exploration.columns.filter((column) => column.visible),
    [exploration.columns],
  );
  const initialFields = exploration.default_field_ids.length > 0
    ? exploration.default_field_ids
    : visibleColumns.slice(0, 1).map((column) => column.field_id);
  const [selectedFields, setSelectedFields] = useState<string[]>(initialFields);
  const [drafts, setDrafts] = useState<FilterDraft[]>([]);
  const [appliedFilters, setAppliedFilters] = useState<SnapshotFilter[]>([]);
  const [filterError, setFilterError] = useState<string | null>(null);
  const [sorts, setSorts] = useState<SnapshotSort[]>([]);
  const [pageSize, setPageSize] = useState(50);
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const [pageIndex, setPageIndex] = useState(0);
  const cursor = cursors[pageIndex] ?? null;
  const request: SnapshotRowsQuery = useMemo(
    () => ({ fields: selectedFields, filters: appliedFilters, sorts, page_size: pageSize, cursor }),
    [selectedFields, appliedFilters, sorts, pageSize, cursor],
  );
  const rows = useQuery({
    queryKey: queryKeys.datasetRows(exploration.dataset_id, request),
    queryFn: () => queryDatasetRows(exploration.dataset_id, request),
  });

  function resetPagination() {
    setCursors([null]);
    setPageIndex(0);
  }

  function toggleField(fieldId: string) {
    setSelectedFields((current) => {
      if (current.includes(fieldId)) {
        return current.length === 1 ? current : current.filter((id) => id !== fieldId);
      }
      return [...current, fieldId];
    });
    resetPagination();
  }

  function applyFilters() {
    try {
      setAppliedFilters(buildSnapshotFilters(drafts, visibleColumns));
      setFilterError(null);
      resetPagination();
    } catch (error) {
      setFilterError(error instanceof Error ? t(error.message) : t("Review the filter values."));
    }
  }

  function clearFilters() {
    setDrafts([]);
    setAppliedFilters([]);
    setFilterError(null);
    resetPagination();
  }

  function cycleSort(column: DatasetColumn) {
    if (!column.sortable) return;
    const existing = sorts.find((sort) => sort.field_id === column.field_id);
    let next: SnapshotSort[];
    if (!existing) {
      if (sorts.length >= 5) return;
      next = [...sorts, { field_id: column.field_id, direction: "asc" }];
    } else if (existing.direction === "asc") {
      next = sorts.map((sort) => sort.field_id === column.field_id ? { ...sort, direction: "desc" } : sort);
    } else {
      next = sorts.filter((sort) => sort.field_id !== column.field_id);
    }
    setSorts(next);
    resetPagination();
  }

  function goNext() {
    const nextCursor = rows.data?.meta.next_cursor;
    if (!nextCursor) return;
    setCursors((current) => [...current.slice(0, pageIndex + 1), nextCursor]);
    setPageIndex((current) => current + 1);
  }

  const selectedColumns = selectedFields
    .map((fieldId) => visibleColumns.find((column) => column.field_id === fieldId))
    .filter((column): column is DatasetColumn => column !== undefined);
  const activeSnapshotQuery = { fields: selectedFields, filters: appliedFilters, sorts };

  return (
    <div className="page-stack dataset-workspace">
      <Link className="back-link" to="/datasets"><ArrowLeft size={16} /> {t("All datasets")}</Link>
      <header className="page-heading dataset-titlebar">
        <div>
          <span className="eyebrow">{t("Snapshot table")}</span>
          <h1>{exploration.dataset_name}</h1>
          <p>
            {t("{count} rows · Snapshot activated {date}", { count: exploration.snapshot_row_count.toLocaleString(), date: formatDateTime(exploration.snapshot_activated_at, locale) })}
          </p>
        </div>
        <div className="heading-actions">
          <span className={`freshness-pill ${exploration.is_stale ? "stale" : "fresh"}`}>
            {exploration.is_stale ? <TriangleAlert size={15} /> : <Check size={15} />}
            {t(exploration.is_stale ? "Showing last good snapshot" : "Snapshot current")}
          </span>
          {exploration.can_export && <ExportPanel datasetId={exploration.dataset_id} query={activeSnapshotQuery} />}
        </div>
      </header>

      {exploration.last_refresh_error && (
        <div className="snapshot-warning" role="status">
          <TriangleAlert size={18} />
          <div><strong>{t("The latest refresh failed; these rows remain safe to use.")}</strong><p>{exploration.last_refresh_error}</p></div>
        </div>
      )}

      <DatasetFilterBar
        columns={visibleColumns}
        drafts={drafts}
        error={filterError}
        onChange={(next) => { setDrafts(next); setFilterError(null); }}
        onApply={applyFilters}
        onClear={clearFilters}
      />

      <section className="table-card" aria-label={t("Dataset rows")}>
        <div className="table-toolbar">
          <div>
            <span className="panel-icon"><Rows3 size={16} /></span>
            <strong>{t("Rows")}</strong>
            {appliedFilters.length > 0 && <span className="count-badge">{t("{count} filters", { count: appliedFilters.length })}</span>}
          </div>
          <div className="table-controls">
            <details className="column-picker">
              <summary><Columns3 size={16} /> {t("{count} columns", { count: selectedFields.length })}</summary>
              <div>
                <strong>{t("Visible columns")}</strong>
                <small>{t("At least one column is required.")}</small>
                {visibleColumns.map((column) => (
                  <label key={column.field_id}>
                    <input type="checkbox" checked={selectedFields.includes(column.field_id)} onChange={() => toggleField(column.field_id)} />
                    <span>{column.label}<small>{column.data_type}</small></span>
                  </label>
                ))}
              </div>
            </details>
            <label className="page-size"><span>{t("Rows per page")}</span><select value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value)); resetPagination(); }}><option value={50}>50</option><option value={100}>100</option><option value={200}>200</option></select></label>
            <button className="icon-button bordered" type="button" aria-label={t("Refresh rows")} onClick={() => void rows.refetch()} disabled={rows.isFetching}><RefreshCw className={rows.isFetching ? "spin" : ""} size={16} /></button>
          </div>
        </div>

        {appliedFilters.length > 0 && (
          <div className="applied-filters" aria-label={t("Applied filters")}>
            {appliedFilters.map((filter, index) => {
              const column = visibleColumns.find((candidate) => candidate.field_id === filter.field_id);
              return <span key={`${filter.field_id}-${index}`}>{column?.label ?? t("Column")} {t(operatorLabels[filter.operator])} {filterValueLabel(filter)}</span>;
            })}
          </div>
        )}

        {rows.isError && <InlineError message={problemMessage(rows.error)} onRetry={() => void rows.refetch()} />}
        {!rows.isError && (
          <div className={`dataset-table-scroll ${rows.isFetching ? "loading" : ""}`}>
            <table>
              <thead><tr>{selectedColumns.map((column) => {
                const sort = sorts.find((candidate) => candidate.field_id === column.field_id);
                const SortIcon = sort?.direction === "asc" ? ArrowUp : sort?.direction === "desc" ? ArrowDown : ArrowUpDown;
                return (
                  <th key={column.field_id} aria-sort={sort?.direction === "asc" ? "ascending" : sort?.direction === "desc" ? "descending" : "none"}>
                    <button type="button" disabled={!column.sortable} onClick={() => cycleSort(column)} title={t(column.sortable ? "Cycle sort order" : "This column cannot be sorted")}>
                      <span>{column.label}<small>{column.data_type}</small></span><SortIcon size={14} />
                    </button>
                  </th>
                );
              })}</tr></thead>
              <tbody>
                {rows.isPending && Array.from({ length: 8 }, (_, rowIndex) => <tr key={rowIndex}>{selectedColumns.map((column) => <td key={column.field_id}><span className="cell-skeleton" /></td>)}</tr>)}
                {rows.data?.data.rows.map((row, rowIndex) => <tr key={`${pageIndex}-${rowIndex}`}>{selectedColumns.map((column) => <td key={column.field_id} title={formatDatasetValue(row[column.name], column, exploration.installation_timezone)}>{formatDatasetValue(row[column.name], column, exploration.installation_timezone)}</td>)}</tr>)}
              </tbody>
            </table>
            {!rows.isPending && rows.data?.data.rows.length === 0 && <div className="empty-state table-empty"><DatabaseZap /><strong>{t("No matching rows")}</strong><p>{t("Adjust the filters and apply them again.")}</p></div>}
          </div>
        )}

        {rows.data?.meta.truncated_by_bytes && <div className="table-notice"><TriangleAlert size={15} /> {t("This page reached the response-size safety limit. Continue with the next page or narrow the result.")}</div>}
        <footer className="table-pagination">
          <span>{t("Page {page} · {count} rows shown", { page: pageIndex + 1, count: rows.data?.data.rows.length ?? 0 })}</span>
          <div>
            <button className="button secondary small" type="button" disabled={pageIndex === 0 || rows.isFetching} onClick={() => setPageIndex((current) => current - 1)}><ChevronLeft size={15} /> {t("Previous")}</button>
            <button className="button secondary small" type="button" disabled={!rows.data?.meta.next_cursor || rows.isFetching} onClick={goNext}>{t("Next")} <ChevronRight size={15} /></button>
          </div>
        </footer>
      </section>
    </div>
  );
}

function filterValueLabel(filter: SnapshotFilter): string {
  if (filter.operator === "is_null" || filter.operator === "is_not_null") return "";
  if (filter.values) return filter.values.map(String).join(filter.operator === "between" ? " – " : "; ");
  return String(filter.value ?? "");
}
