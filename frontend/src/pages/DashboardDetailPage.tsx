import { useIsFetching, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CalendarClock, RefreshCw, Share2 } from "lucide-react";
import { useState } from "react";
import { Link, useOutletContext, useParams, useSearchParams } from "react-router-dom";

import { DashboardFilterBar } from "../components/DashboardFilterBar";
import { DashboardItemCard } from "../components/DashboardItemCard";
import { FullPageStatus, InlineError } from "../components/Status";
import { getDashboard, problemMessage, type CurrentUser } from "../lib/api";
import { readSelections, selectionsForItem, writeSelections } from "../lib/dashboardFilters";
import { formatDateTime } from "../lib/format";
import { useI18n } from "../lib/i18n";
import { queryKeys } from "../lib/query";

export function DashboardDetailPage() {
  const { locale, t } = useI18n();
  const user = useOutletContext<CurrentUser>();
  const { dashboardId = "" } = useParams();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [checkedAt, setCheckedAt] = useState(() => new Date());
  const dashboard = useQuery({
    queryKey: queryKeys.dashboard(dashboardId),
    queryFn: () => getDashboard(dashboardId),
    enabled: Boolean(dashboardId),
  });
  // The layout and every chart item share the ["dashboard", id] key prefix.
  const refreshing = useIsFetching({ queryKey: queryKeys.dashboard(dashboardId) }) > 0;
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: queryKeys.dashboard(dashboardId) });
    setCheckedAt(new Date());
  };

  if (dashboard.isPending) return <FullPageStatus label={t("Opening dashboard")} />;
  if (dashboard.isError) return <InlineError message={problemMessage(dashboard.error)} onRetry={() => void dashboard.refetch()} />;
  const items = dashboard.data.definition.items ?? [];
  const filters = dashboard.data.definition.filters ?? [];
  const selections = readSelections(params, filters);

  return (
    <div className="page-stack dashboard-detail">
      <Link className="back-link" to="/dashboards"><ArrowLeft size={16} /> {t("All dashboards")}</Link>
      <header className="page-heading dashboard-titlebar">
        <div><h1>{dashboard.data.name}</h1><p><CalendarClock size={15} /> {t("Charts checked at {time}", { time: checkedAt.toLocaleTimeString(locale === "ru" ? "ru-RU" : "en-GB", { hour: "2-digit", minute: "2-digit" }) })} · {t("Dashboard changed {date}", { date: formatDateTime(dashboard.data.updated_at, locale) })}</p></div>
        <div className="heading-actions">
          <button
            className="button secondary"
            type="button"
            disabled={refreshing}
            onClick={() => void refresh()}
            title={t("Shows the latest loaded data. Data is loaded from the database on the dataset page or by its schedule.")}
          >
            <RefreshCw size={16} className={refreshing ? "spin" : undefined} /> {t("Refresh charts")}
          </button>
          {(user.role === "admin" || dashboard.data.owner_id === user.id) && <Link className="button secondary" to={`/dashboards/${dashboard.data.id}/edit`}><Share2 size={16} /> {t("Edit & share")}</Link>}
        </div>
      </header>
      {filters.length > 0 && (
        <DashboardFilterBar
          dashboardId={dashboard.data.id}
          filters={filters}
          selections={selections}
          onChange={(next) => setParams(writeSelections(params, next), { replace: true })}
        />
      )}
      {items.length > 0 ? (
        <section className="dashboard-grid" aria-label={t("{name} visualizations", { name: dashboard.data.name })}>
          {items.map((item) => <DashboardItemCard dashboardId={dashboard.data.id} item={item} filters={selectionsForItem(item.id, filters, selections)} key={item.id} />)}
        </section>
      ) : (
        <div className="empty-state dashboard-empty"><strong>{t("This dashboard is intentionally empty")}</strong><p>{t("An analyst can add saved charts in the dashboard studio.")}</p></div>
      )}
    </div>
  );
}
