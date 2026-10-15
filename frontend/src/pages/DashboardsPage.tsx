import { useInfiniteQuery } from "@tanstack/react-query";
import { ArrowRight, LayoutDashboard, Plus, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { InlineError } from "../components/Status";
import { listDashboards, problemMessage } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { queryKeys } from "../lib/query";

export function DashboardsPage() {
  const [search, setSearch] = useState("");
  const dashboards = useInfiniteQuery({
    queryKey: queryKeys.dashboards,
    queryFn: ({ pageParam }) => listDashboards(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.meta.next_cursor ?? undefined,
  });
  const values = useMemo(
    () =>
      dashboards.data?.pages
        .flatMap((page) => page.data)
        .filter((dashboard) => dashboard.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())) ?? [],
    [dashboards.data, search],
  );

  return (
    <div className="page-stack">
      <header className="page-heading split-heading">
        <div><span className="eyebrow">Shared understanding</span><h1>Dashboards</h1><p>Every visualization loads and reports freshness independently.</p></div>
        <button className="button primary" disabled title="Dashboard editing arrives in the dashboard studio slice"><Plus size={17} /> New dashboard</button>
      </header>
      <div className="collection-toolbar">
        <label className="search-field"><Search size={17} /><span className="sr-only">Search dashboards</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search dashboards" /></label>
        <span>{values.length} visible</span>
      </div>
      {dashboards.isError && <InlineError message={problemMessage(dashboards.error)} onRetry={() => void dashboards.refetch()} />}
      <section className="dashboard-cards" aria-label="Dashboards">
        {dashboards.isPending && [0, 1, 2, 3, 4, 5].map((item) => <div className="dashboard-card skeleton" key={item} />)}
        {values.map((dashboard) => (
          <Link className="dashboard-card" key={dashboard.id} to={`/dashboards/${dashboard.id}`}>
            <div className="dashboard-card-top"><span className="dashboard-icon"><LayoutDashboard size={20} /></span><span className="revision">v{dashboard.revision}</span></div>
            <div><h2>{dashboard.name}</h2><p>{dashboard.definition.items?.length ?? 0} visualizations</p></div>
            <footer><time dateTime={dashboard.updated_at}>Updated {formatDateTime(dashboard.updated_at)}</time><ArrowRight size={17} /></footer>
          </Link>
        ))}
      </section>
      {!dashboards.isPending && values.length === 0 && (
        <div className="empty-state"><LayoutDashboard /><strong>{search ? "No matching dashboards" : "No dashboards are available"}</strong><p>{search ? "Try a different name." : "Ask an analyst to share a dashboard with you."}</p></div>
      )}
      {dashboards.hasNextPage && <button className="button secondary load-more" onClick={() => void dashboards.fetchNextPage()} disabled={dashboards.isFetchingNextPage}>{dashboards.isFetchingNextPage ? "Loading…" : "Load more"}</button>}
    </div>
  );
}
