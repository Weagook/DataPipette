import { useInfiniteQuery } from "@tanstack/react-query";
import { ArrowRight, Database, Layers3, Plus, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, Navigate, useOutletContext } from "react-router-dom";

import { InlineError } from "../components/Status";
import { listDatasets, problemMessage, type CurrentUser } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { useI18n } from "../lib/i18n";
import { queryKeys } from "../lib/query";

export function DatasetsPage() {
  const { locale, t } = useI18n();
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
      <section className="dataset-cards" aria-label={t("Datasets")}>
        {datasets.isPending &&
          [0, 1, 2, 3, 4, 5].map((item) => (
            <div className="dataset-card skeleton" key={item} />
          ))}
        {values.map((dataset) => {
          const published = dataset.active_snapshot_id !== null;
          const content = (
            <>
              <div className="dataset-card-top">
                <span className="dataset-icon">
                  <Database size={20} />
                </span>
                <span className={`state-pill ${published ? "ready" : "draft"}`}>
                  {t(published ? "Snapshot ready" : "Draft only")}
                </span>
              </div>
              <div className="dataset-card-copy">
                <h2>{dataset.name}</h2>
                <p>{t("Draft revision {revision}", { revision: dataset.draft_revision })}</p>
              </div>
              <footer>
                <time dateTime={dataset.updated_at}>
                  {t("Updated {date}", { date: formatDateTime(dataset.updated_at, locale) })}
                </time>
                {published ? <ArrowRight size={17} /> : <Layers3 size={17} />}
              </footer>
            </>
          );
          return (
            <Link
              className="dataset-card"
              key={dataset.id}
              to={`/datasets/${dataset.id}/draft`}
            >
              {content}
            </Link>
          );
        })}
      </section>
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
