import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, BarChart3, Database, LayoutDashboard } from "lucide-react";
import { Link, useOutletContext } from "react-router-dom";

import { InlineError } from "../components/Status";
import { listDashboards, problemMessage, type CurrentUser } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { useI18n } from "../lib/i18n";
import { queryKeys } from "../lib/query";

export function OverviewPage() {
  const { locale, t } = useI18n();
  const user = useOutletContext<CurrentUser | undefined>();
  const dashboards = useQuery({
    queryKey: queryKeys.dashboardOverview,
    queryFn: () => listDashboards(),
  });

  return (
    <div className="page-stack">
      <header className="page-heading overview-heading">
        <div>
          <h1>{t("Overview")}</h1>
          <p>{t("Open a dashboard, or prepare new data and charts.")}</p>
        </div>
      </header>

      {dashboards.isError ? (
        <InlineError message={problemMessage(dashboards.error)} onRetry={() => void dashboards.refetch()} />
      ) : (
        <section className="overview-grid" aria-label={t("Workspace summary")}>
          <article className="metric-card primary-metric">
            <span>{t("Dashboards you can open")}</span>
            <strong>{dashboards.data?.data.length ?? "—"}</strong>
            <Link to="/dashboards">{t("Open dashboards")} <ArrowUpRight size={16} /></Link>
          </article>
          {user && user.role !== "viewer" && (
            <>
              <Link className="metric-card action-card" to="/datasets/new">
                <Database />
                <strong>{t("New dataset")}</strong>
                <p>{t("Pick tables from your database and load them for analysis.")}</p>
              </Link>
              <Link className="metric-card action-card" to="/charts/new">
                <BarChart3 />
                <strong>{t("New chart")}</strong>
                <p>{t("Turn a dataset into a bar, line, pie, or single-number chart.")}</p>
              </Link>
            </>
          )}
        </section>
      )}

      <section className="section-block">
        <div className="section-heading">
          <div><h2>{t("Recent dashboards")}</h2></div>
          <Link className="text-link" to="/dashboards">{t("View all")} <ArrowUpRight size={15} /></Link>
        </div>
        <div className="recent-list">
          {dashboards.isPending && [0, 1, 2].map((item) => <div className="dashboard-row skeleton" key={item} />)}
          {dashboards.data?.data.slice(0, 4).map((dashboard) => (
            <Link className="dashboard-row" to={`/dashboards/${dashboard.id}`} key={dashboard.id}>
              <span className="dashboard-icon"><LayoutDashboard size={19} /></span>
              <span><strong>{dashboard.name}</strong><small>{t("{count} visualizations", { count: dashboard.definition.items?.length ?? 0 })}</small></span>
              <time dateTime={dashboard.updated_at}>{formatDateTime(dashboard.updated_at, locale)}</time>
              <ArrowUpRight size={17} />
            </Link>
          ))}
          {dashboards.data?.data.length === 0 && (
            <div className="empty-state compact"><LayoutDashboard /><strong>{t("No dashboards yet")}</strong><p>{t("Shared dashboards will appear here.")}</p></div>
          )}
        </div>
      </section>

    </div>
  );
}
