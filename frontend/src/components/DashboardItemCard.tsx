import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Clock3, DatabaseZap, MoreHorizontal, RotateCcw } from "lucide-react";
import { lazy, Suspense } from "react";

import { problemMessage, queryDashboardItem, type Dashboard } from "../lib/api";
import { formatDateTime, relativeTime } from "../lib/format";
import { queryKeys } from "../lib/query";
const ChartView = lazy(() =>
  import("./ChartView").then((module) => ({ default: module.ChartView })),
);

type DashboardItem = NonNullable<Dashboard["definition"]["items"]>[number];

export function DashboardItemCard({ dashboardId, item }: { dashboardId: string; item: DashboardItem }) {
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
              {query.data.meta.is_stale ? "Stale snapshot" : `Updated ${relativeTime(query.data.meta.snapshot_activated_at)}`}
            </p>
          )}
        </div>
        <button className="icon-button quiet" type="button" aria-label="Chart options" disabled><MoreHorizontal size={18} /></button>
      </header>
      <div className="chart-card-body">
        {query.isPending && <ChartSkeleton />}
        {query.isError && (
          <div className="chart-failure" role="alert">
            <DatabaseZap size={25} />
            <strong>Visualization unavailable</strong>
            <p>{problemMessage(query.error)}</p>
            <button className="button secondary small" onClick={() => void query.refetch()} type="button"><RotateCcw size={15} /> Retry</button>
          </div>
        )}
        {query.data && query.data.data.rows.length === 0 && (
          <div className="chart-failure empty-chart"><DatabaseZap size={25} /><strong>No matching data</strong><p>The saved chart filters returned no rows.</p></div>
        )}
        {query.data && query.data.data.rows.length > 0 && (
          <Suspense fallback={<ChartSkeleton />}><ChartView result={query.data} /></Suspense>
        )}
      </div>
      {query.data?.meta.is_stale && (
        <footer className="stale-notice"><AlertTriangle size={15} /><span>Showing the last successful snapshot. {query.data.meta.last_refresh_error ?? "The latest refresh failed."}</span></footer>
      )}
      {query.data?.meta.truncated_by_bytes && (
        <footer className="truncated-notice"><Clock3 size={15} /> Result was truncated by the response safety limit.</footer>
      )}
      {query.data && <span className="sr-only">Snapshot activated {formatDateTime(query.data.meta.snapshot_activated_at)}</span>}
    </article>
  );
}

function ChartSkeleton() {
  return <div className="chart-skeleton" aria-label="Loading visualization"><i /><i /><i /><i /><i /></div>;
}
