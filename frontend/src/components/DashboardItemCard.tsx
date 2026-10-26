import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Clock3, DatabaseZap, MoreHorizontal, RotateCcw } from "lucide-react";
import { lazy, Suspense } from "react";

import { problemMessage, queryDashboardItem, type Dashboard } from "../lib/api";
import { formatDateTime, relativeTime } from "../lib/format";
import { useI18n } from "../lib/i18n";
import { queryKeys } from "../lib/query";
const ChartView = lazy(() =>
  import("./ChartView").then((module) => ({ default: module.ChartView })),
);

type DashboardItem = NonNullable<Dashboard["definition"]["items"]>[number];

export function DashboardItemCard({ dashboardId, item }: { dashboardId: string; item: DashboardItem }) {
  const { locale, t } = useI18n();
  const query = useQuery({
    queryKey: queryKeys.dashboardItem(dashboardId, item.id),
    queryFn: () => queryDashboardItem(dashboardId, item.id),
  });

  return (
    <article
      className="chart-card"
      style={{
        gridColumn: `${item.x + 1} / span ${item.width}`,
        gridRow: `${item.y + 1} / span ${Math.max(item.height, 3)}`,
      }}
    >
      <header className="chart-card-head">
        <div>
          <h2>{query.data?.meta.chart_name ?? <span className="title-skeleton" />}</h2>
          {query.data && (
            <p>
              {query.data.meta.is_stale ? t("Stale snapshot") : t("Updated {date}", { date: relativeTime(query.data.meta.snapshot_activated_at, locale) })}
            </p>
          )}
        </div>
        <button className="icon-button quiet" type="button" aria-label={t("Chart options")} disabled><MoreHorizontal size={18} /></button>
      </header>
      <div className="chart-card-body">
        {query.isPending && <ChartSkeleton />}
        {query.isError && (
          <div className="chart-failure" role="alert">
            <DatabaseZap size={25} />
            <strong>{t("Visualization unavailable")}</strong>
            <p>{problemMessage(query.error)}</p>
            <button className="button secondary small" onClick={() => void query.refetch()} type="button"><RotateCcw size={15} /> {t("Retry")}</button>
          </div>
        )}
        {query.data && query.data.data.rows.length === 0 && (
          <div className="chart-failure empty-chart"><DatabaseZap size={25} /><strong>{t("No matching data")}</strong><p>{t("The saved chart filters returned no rows.")}</p></div>
        )}
        {query.data && query.data.data.rows.length > 0 && (
          <Suspense fallback={<ChartSkeleton />}><ChartView result={query.data} /></Suspense>
        )}
      </div>
      {query.data?.meta.is_stale && (
        <footer className="stale-notice"><AlertTriangle size={15} /><span>{t("Showing the last successful snapshot.")} {query.data.meta.last_refresh_error ?? t("The latest refresh failed.")}</span></footer>
      )}
      {query.data?.meta.truncated_by_bytes && (
        <footer className="truncated-notice"><Clock3 size={15} /> {t("Result was truncated by the response safety limit.")}</footer>
      )}
      {query.data && <span className="sr-only">{t("Snapshot activated {date}", { date: formatDateTime(query.data.meta.snapshot_activated_at, locale) })}</span>}
    </article>
  );
}

function ChartSkeleton() {
  const { t } = useI18n();
  return <div className="chart-skeleton" aria-label={t("Loading visualization")}><i /><i /><i /><i /><i /></div>;
}
