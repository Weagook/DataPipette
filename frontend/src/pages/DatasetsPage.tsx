import { useInfiniteQuery } from "@tanstack/react-query";
import { ArrowRight, Database, Layers3, Plus, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, Navigate, useOutletContext } from "react-router-dom";

import { InlineError } from "../components/Status";
import { listDatasets, problemMessage, type CurrentUser } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { queryKeys } from "../lib/query";

export function DatasetsPage() {
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
          <span className="eyebrow">Reusable data models</span>
          <h1>Datasets</h1>
          <p>
            Open a published snapshot, shape the table for your question, and
            export exactly the rows you can see.
          </p>
        </div>
        <Link className="button primary" to="/datasets/new">
          <Plus size={17} /> New dataset
        </Link>
      </header>
      <div className="collection-toolbar">
        <label className="search-field">
          <Search size={17} />
          <span className="sr-only">Search datasets</span>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search datasets"
          />
        </label>
        <span>{values.length} visible</span>
      </div>
      {datasets.isError && (
        <InlineError
          message={problemMessage(datasets.error)}
          onRetry={() => void datasets.refetch()}
        />
      )}
      <section className="dataset-cards" aria-label="Datasets">
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
                  {published ? "Snapshot ready" : "Draft only"}
                </span>
              </div>
              <div className="dataset-card-copy">
                <h2>{dataset.name}</h2>
                <p>Draft revision {dataset.draft_revision}</p>
              </div>
              <footer>
                <time dateTime={dataset.updated_at}>
                  Updated {formatDateTime(dataset.updated_at)}
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
            {search ? "No matching datasets" : "No authorable datasets yet"}
          </strong>
          <p>
            {search
              ? "Try a different name."
              : "Published datasets will appear here."}
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
          {datasets.isFetchingNextPage ? "Loading…" : "Load more"}
        </button>
      )}
    </div>
  );
}
