import { useInfiniteQuery } from "@tanstack/react-query";
import { ArrowRight, LayoutDashboard, Plus, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useOutletContext } from "react-router-dom";

import { InlineError } from "../components/Status";
import { listDashboards, problemMessage, type CurrentUser } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { useI18n } from "../lib/i18n";
import { queryKeys } from "../lib/query";

export function DashboardsPage() {
  const { locale, t } = useI18n();
  const user = useOutletContext<CurrentUser>();
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
        <div><h1>{t("Dashboards")}</h1><p>{t("Pages that bring several charts together.")}</p></div>
        {user.role !== "viewer" && <Link className="button primary" to="/dashboards/new"><Plus size={17} /> {t("New dashboard")}</Link>}
      </header>
      <div className="collection-toolbar">
        <label className="search-field"><Search size={17} /><span className="sr-only">{t("Search dashboards")}</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t("Search dashboards")} /></label>
        <span>{t("{count} visible", { count: values.length })}</span>
      </div>
      {dashboards.isError && <InlineError message={problemMessage(dashboards.error)} onRetry={() => void dashboards.refetch()} />}
      <section className="dashboard-cards" aria-label={t("Dashboards")}>
        {dashboards.isPending && [0, 1, 2, 3, 4, 5].map((item) => <div className="dashboard-card skeleton" key={item} />)}
        {values.map((dashboard) => (
          <Link className="dashboard-card" key={dashboard.id} to={user.role !== "viewer" && (user.role === "admin" || dashboard.owner_id === user.id) ? `/dashboards/${dashboard.id}/edit` : `/dashboards/${dashboard.id}`}>
            <div className="dashboard-card-top"><span className="dashboard-icon"><LayoutDashboard size={20} /></span><span className="revision">v{dashboard.revision}</span></div>
            <div><h2>{dashboard.name}</h2><p>{t("{count} visualizations", { count: dashboard.definition.items?.length ?? 0 })}</p></div>
            <footer><time dateTime={dashboard.updated_at}>{t("Updated {date}", { date: formatDateTime(dashboard.updated_at, locale) })}</time><ArrowRight size={17} /></footer>
          </Link>
        ))}
      </section>
      {!dashboards.isPending && values.length === 0 && (
        <div className="empty-state"><LayoutDashboard /><strong>{t(search ? "No matching dashboards" : "No dashboards are available")}</strong><p>{t(search ? "Try a different name." : "Ask an analyst to share a dashboard with you.")}</p></div>
      )}
      {dashboards.hasNextPage && <button className="button secondary load-more" onClick={() => void dashboards.fetchNextPage()} disabled={dashboards.isFetchingNextPage}>{t(dashboards.isFetchingNextPage ? "Loading…" : "Load more")}</button>}
    </div>
  );
}
