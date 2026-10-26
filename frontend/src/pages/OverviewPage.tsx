import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Database, LayoutDashboard, ShieldCheck, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";

import { InlineError } from "../components/Status";
import { listDashboards, problemMessage } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { useI18n } from "../lib/i18n";
import { queryKeys } from "../lib/query";

export function OverviewPage() {
  const { locale, t } = useI18n();
  const dashboards = useQuery({
    queryKey: queryKeys.dashboardOverview,
    queryFn: () => listDashboards(),
  });

  return (
    <div className="page-stack">
      <header className="page-heading overview-heading">
        <div>
          <span className="eyebrow">{t("Today’s workspace")}</span>
          <h1>{t("Good data should feel quiet.")}</h1>
          <p>{t("Start from a trusted dashboard. Freshness and failures stay visible while every chart loads independently.")}</p>
        </div>
        <div className="trust-pill"><ShieldCheck size={17} /> {t("Snapshot reads active")}</div>
      </header>

      {dashboards.isError ? (
        <InlineError message={problemMessage(dashboards.error)} onRetry={() => void dashboards.refetch()} />
      ) : (
        <section className="overview-grid" aria-label={t("Workspace summary")}>
          <article className="metric-card primary-metric">
            <span>{t("Accessible dashboards")}</span>
            <strong>{dashboards.data?.data.length ?? "—"}</strong>
            <Link to="/dashboards">{t("Open collection")} <ArrowUpRight size={16} /></Link>
          </article>
          <article className="metric-card">
            <span>{t("Analytics engine")}</span>
            <strong className="metric-word">{t("Ready")}</strong>
            <p>{t("Queries run against governed Parquet snapshots.")}</p>
          </article>
          <article className="metric-card">
            <span>{t("Access model")}</span>
            <strong className="metric-word">{t("Private")}</strong>
            <p>{t("Resources remain owner-only until explicitly shared.")}</p>
          </article>
        </section>
      )}

      <section className="section-block">
        <div className="section-heading">
          <div><span className="eyebrow">{t("Continue reading")}</span><h2>{t("Recent dashboards")}</h2></div>
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

      <section className="capability-strip" aria-label={t("DataPipette workflow")}>
        <div><Database /><span><strong>{t("Connect")}</strong><small>{t("PostgreSQL sources")}</small></span></div>
        <i />
        <div><Sparkles /><span><strong>{t("Shape")}</strong><small>{t("Visual or SQL datasets")}</small></span></div>
        <i />
        <div><LayoutDashboard /><span><strong>{t("Share")}</strong><small>{t("Governed insight")}</small></span></div>
      </section>
    </div>
  );
}
