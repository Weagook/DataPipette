import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Database, LayoutDashboard, ShieldCheck, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";

import { InlineError } from "../components/Status";
import { listDashboards, problemMessage } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { queryKeys } from "../lib/query";

export function OverviewPage() {
  const dashboards = useQuery({
    queryKey: queryKeys.dashboardOverview,
    queryFn: () => listDashboards(),
  });

  return (
    <div className="page-stack">
      <header className="page-heading overview-heading">
        <div>
          <span className="eyebrow">Today’s workspace</span>
          <h1>Good data should feel quiet.</h1>
          <p>Start from a trusted dashboard. Freshness and failures stay visible while every chart loads independently.</p>
        </div>
        <div className="trust-pill"><ShieldCheck size={17} /> Snapshot reads active</div>
      </header>

      {dashboards.isError ? (
        <InlineError message={problemMessage(dashboards.error)} onRetry={() => void dashboards.refetch()} />
      ) : (
        <section className="overview-grid" aria-label="Workspace summary">
          <article className="metric-card primary-metric">
            <span>Accessible dashboards</span>
            <strong>{dashboards.data?.data.length ?? "—"}</strong>
            <Link to="/dashboards">Open collection <ArrowUpRight size={16} /></Link>
          </article>
          <article className="metric-card">
            <span>Analytics engine</span>
            <strong className="metric-word">Ready</strong>
            <p>Queries run against governed Parquet snapshots.</p>
          </article>
          <article className="metric-card">
            <span>Access model</span>
            <strong className="metric-word">Private</strong>
            <p>Resources remain owner-only until explicitly shared.</p>
          </article>
        </section>
      )}

      <section className="section-block">
        <div className="section-heading">
          <div><span className="eyebrow">Continue reading</span><h2>Recent dashboards</h2></div>
          <Link className="text-link" to="/dashboards">View all <ArrowUpRight size={15} /></Link>
        </div>
        <div className="recent-list">
          {dashboards.isPending && [0, 1, 2].map((item) => <div className="dashboard-row skeleton" key={item} />)}
          {dashboards.data?.data.slice(0, 4).map((dashboard) => (
            <Link className="dashboard-row" to={`/dashboards/${dashboard.id}`} key={dashboard.id}>
              <span className="dashboard-icon"><LayoutDashboard size={19} /></span>
              <span><strong>{dashboard.name}</strong><small>{dashboard.definition.items?.length ?? 0} visualizations</small></span>
              <time dateTime={dashboard.updated_at}>{formatDateTime(dashboard.updated_at)}</time>
              <ArrowUpRight size={17} />
            </Link>
          ))}
          {dashboards.data?.data.length === 0 && (
            <div className="empty-state compact"><LayoutDashboard /><strong>No dashboards yet</strong><p>Shared dashboards will appear here.</p></div>
          )}
        </div>
      </section>

      <section className="capability-strip" aria-label="DatePipette workflow">
        <div><Database /><span><strong>Connect</strong><small>PostgreSQL sources</small></span></div>
        <i />
        <div><Sparkles /><span><strong>Shape</strong><small>Visual or SQL datasets</small></span></div>
        <i />
        <div><LayoutDashboard /><span><strong>Share</strong><small>Governed insight</small></span></div>
      </section>
    </div>
  );
}
