import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { BarChart3, Database, Pencil, Plus, Search, Table2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, Navigate, useOutletContext } from "react-router-dom";

import { InlineError } from "../components/Status";
import {
  getDatasetFreshness,
  listDatasets,
  problemMessage,
  type CurrentUser,
  type Dataset,
} from "../lib/api";
import { formatDateTime, relativeTime } from "../lib/format";
import { useI18n } from "../lib/i18n";
import { queryKeys } from "../lib/query";

export function DatasetsPage() {
  const { t } = useI18n();
  const user = useOutletContext<CurrentUser>();
  const [search, setSearch] = useState("");
  const datasets = useInfiniteQuery({
    queryKey: queryKeys.datasets,
    queryFn: ({ pageParam }) => listDatasets(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.meta.next_cursor ?? undefined,
  });
  const values = useMemo(
    () =>
      datasets.data?.pages
        .flatMap((page) => page.data)
        .filter((dataset) =>
          dataset.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
        ) ?? [],
    [datasets.data, search],
  );

  if (user.role === "viewer") return <Navigate to="/overview" replace />;

  return (
    <div className="page-stack">
      <header className="page-heading split-heading">
        <div>
          <h1>{t("Datasets")}</h1>
          <p>
            {t("Prepared tables of data. Open one to view, filter, or export its rows.")}
          </p>
        </div>
        <Link className="button primary" to="/datasets/new">
          <Plus size={17} /> {t("New dataset")}
        </Link>
      </header>
      <div className="collection-toolbar">
        <label className="search-field">
          <Search size={17} />
          <span className="sr-only">{t("Search datasets")}</span>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t("Search datasets")}
          />
        </label>
        <span>{t("{count} visible", { count: values.length })}</span>
      </div>
      {datasets.isError && (
        <InlineError
          message={problemMessage(datasets.error)}
          onRetry={() => void datasets.refetch()}
        />
      )}
      {(datasets.isPending || values.length > 0) && (
        <div className="dataset-list-scroll">
          <table className="dataset-list" aria-label={t("Datasets")}>
            <thead>
              <tr>
                <th>{t("Name")}</th>
                <th>{t("Status")}</th>
                <th className="numeric">{t("Rows")}</th>
                <th>{t("Data loaded")}</th>
                <th><span className="sr-only">{t("Actions")}</span></th>
              </tr>
            </thead>
            <tbody>
              {datasets.isPending &&
                [0, 1, 2].map((item) => (
                  <tr key={item}><td colSpan={5}><span className="cell-skeleton" /></td></tr>
                ))}
              {values.map((dataset) => (
                <DatasetRow key={dataset.id} dataset={dataset} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!datasets.isPending && values.length === 0 && (
        <div className="empty-state">
          <Database />
          <strong>
            {t(search ? "No matching datasets" : "No authorable datasets yet")}
          </strong>
          <p>
            {search
              ? t("Try a different name.")
              : t("Published datasets will appear here.")}
          </p>
        </div>
      )}
      {datasets.hasNextPage && (
        <button
          className="button secondary load-more"
          onClick={() => void datasets.fetchNextPage()}
          disabled={datasets.isFetchingNextPage}
          type="button"
        >
          {t(datasets.isFetchingNextPage ? "Loading…" : "Load more")}
        </button>
      )}
    </div>
  );
}

const STATUS: Record<string, { tone: string; label: string }> = {
  not_published: { tone: "draft", label: "Not loaded yet" },
  refreshing: { tone: "working", label: "Loading data…" },
  ready: { tone: "ready", label: "Up to date" },
  stale: { tone: "stale", label: "Last load failed" },
  failed: { tone: "failed", label: "Load failed" },
};

function DatasetRow({ dataset }: { dataset: Dataset }) {
  const { locale, t } = useI18n();
  const loaded = dataset.active_snapshot_id !== null;
  const freshness = useQuery({
    queryKey: queryKeys.datasetFreshness(dataset.id),
    queryFn: () => getDatasetFreshness(dataset.id),
  });
  const state = freshness.data
    ? freshness.data.refresh_state !== "idle"
      ? "refreshing"
      : freshness.data.data_state
    : loaded
      ? "ready"
      : "not_published";
  const status = STATUS[state] ?? STATUS.ready;
  const loadedAt = freshness.data?.last_success_at;
  return (
    <tr>
      <td>
        <Link className="dataset-list-name" to={loaded ? `/datasets/${dataset.id}/table` : `/datasets/${dataset.id}/draft`}>
          <Database size={16} /> {dataset.name}
        </Link>
      </td>
      <td>
        <span className={`dataset-status ${status.tone}`} title={freshness.data?.last_refresh_error ?? undefined}>
          {t(status.label)}
        </span>
      </td>
      <td className="numeric">
        {freshness.data?.snapshot_row_count != null
          ? freshness.data.snapshot_row_count.toLocaleString(locale === "ru" ? "ru-RU" : "en-US")
          : "—"}
      </td>
      <td>
        {loadedAt ? <time dateTime={loadedAt} title={formatDateTime(loadedAt, locale)}>{relativeTime(loadedAt, locale)}</time> : "—"}
      </td>
      <td>
        <div className="dataset-list-actions">
          {loaded && (
            <Link className="button ghost small" to={`/datasets/${dataset.id}/table`}>
              <Table2 size={14} /> {t("Open")}
            </Link>
          )}
          <Link className="button ghost small" to={`/datasets/${dataset.id}/draft`}>
            <Pencil size={14} /> {t("Edit")}
          </Link>
          {loaded && (
            <Link className="button ghost small" to={`/charts/new?dataset=${dataset.id}`}>
              <BarChart3 size={14} /> {t("Chart")}
            </Link>
          )}
        </div>
      </td>
    </tr>
  );
}
