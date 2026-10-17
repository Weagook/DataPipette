import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, CalendarClock, MoreHorizontal, RefreshCw, Share2 } from "lucide-react";
import { Link, useOutletContext, useParams } from "react-router-dom";

import { DashboardItemCard } from "../components/DashboardItemCard";
import { FullPageStatus, InlineError } from "../components/Status";
import { getDashboard, problemMessage, type CurrentUser } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { useI18n } from "../lib/i18n";
import { queryKeys } from "../lib/query";

export function DashboardDetailPage() {
  const { t } = useI18n();
  const user = useOutletContext<CurrentUser>();
  const { dashboardId = "" } = useParams();
  const dashboard = useQuery({
    queryKey: queryKeys.dashboard(dashboardId),
    queryFn: () => getDashboard(dashboardId),
    enabled: Boolean(dashboardId),
  });

  if (dashboard.isPending) return <FullPageStatus label={t("Opening dashboard")} />;
  if (dashboard.isError) return <InlineError message={problemMessage(dashboard.error)} onRetry={() => void dashboard.refetch()} />;
  const items = dashboard.data.definition.items ?? [];

  return (
    <div className="page-stack dashboard-detail">
      <Link className="back-link" to="/dashboards"><ArrowLeft size={16} /> {t("All dashboards")}</Link>
      <header className="page-heading dashboard-titlebar">
        <div><span className="eyebrow">{t("Dashboard")}</span><h1>{dashboard.data.name}</h1><p><CalendarClock size={15} /> {t("Updated {date} · revision {revision}", { date: formatDateTime(dashboard.data.updated_at), revision: dashboard.data.revision })}</p></div>
        <div className="heading-actions">
          <button className="button secondary" type="button" onClick={() => void dashboard.refetch()}><RefreshCw size={16} /> {t("Refresh view")}</button>
          {(user.role === "admin" || dashboard.data.owner_id === user.id) && <Link className="button secondary" to={`/dashboards/${dashboard.data.id}/edit`}><Share2 size={16} /> {t("Edit & share")}</Link>}
          <button className="icon-button bordered" type="button" disabled aria-label={t("More dashboard actions")}><MoreHorizontal size={18} /></button>
        </div>
      </header>
      {items.length > 0 ? (
        <section className="dashboard-grid" aria-label={t("{name} visualizations", { name: dashboard.data.name })}>
          {items.map((item) => <DashboardItemCard dashboardId={dashboard.data.id} item={item} key={item.id} />)}
        </section>
      ) : (
        <div className="empty-state dashboard-empty"><strong>{t("This dashboard is intentionally empty")}</strong><p>{t("An analyst can add saved charts in the dashboard studio.")}</p></div>
      )}
    </div>
  );
}
