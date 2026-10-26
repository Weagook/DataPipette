import { useInfiniteQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  BarChart3,
  ChartNoAxesCombined,
  Plus,
  Search,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Link, Navigate, useOutletContext } from "react-router-dom";

import { InlineError } from "../components/Status";
import { listCharts, problemMessage, type CurrentUser } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { useI18n } from "../lib/i18n";
import { queryKeys } from "../lib/query";

const typeLabels = {
  bar: "Bar chart",
  line: "Line chart",
  pie: "Pie chart",
  donut: "Donut chart",
  kpi: "KPI",
} as const;

export function ChartsPage() {
  const { locale, t } = useI18n();
  const user = useOutletContext<CurrentUser>();
  const [search, setSearch] = useState("");
  const charts = useInfiniteQuery({
    queryKey: queryKeys.charts,
    queryFn: ({ pageParam }) => listCharts(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.meta.next_cursor ?? undefined,
  });
  const values = useMemo(
    () =>
      charts.data?.pages
        .flatMap((page) => page.data)
        .filter((chart) =>
          chart.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
        ) ?? [],
    [charts.data, search],
  );

  if (user.role === "viewer") return <Navigate to="/overview" replace />;

  return (
    <div className="page-stack">
      <header className="page-heading split-heading">
        <div>
          <span className="eyebrow">{t("Saved analytics")}</span>
          <h1>{t("Charts")}</h1>
          <p>{t("Turn published datasets into governed, reusable visual answers.")}</p>
        </div>
        <Link className="button primary" to="/charts/new">
          <Plus size={17} /> {t("New chart")}
        </Link>
      </header>
      <div className="collection-toolbar">
        <label className="search-field">
          <Search size={17} />
          <span className="sr-only">{t("Search charts")}</span>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t("Search charts")}
          />
        </label>
        <span>{t("{count} visible", { count: values.length })}</span>
      </div>
      {charts.isError && (
        <InlineError
          message={problemMessage(charts.error)}
          onRetry={() => void charts.refetch()}
        />
      )}
      <section className="chart-collection" aria-label={t("Charts")}>
        {charts.isPending &&
          [0, 1, 2, 3, 4, 5].map((item) => (
            <div className="saved-chart-card skeleton" key={item} />
          ))}
        {values.map((chart) => (
          <Link
            className="saved-chart-card"
            key={chart.id}
            to={`/charts/${chart.id}`}
          >
            <div className="saved-chart-visual">
              {chart.definition.chart_type === "kpi" ? (
                <strong>42.8K</strong>
              ) : (
                <ChartNoAxesCombined size={42} />
              )}
            </div>
            <div className="saved-chart-copy">
              <span className="chart-type-pill">
                <BarChart3 size={13} />
                {t(typeLabels[chart.definition.chart_type])}
              </span>
              <h2>{chart.name}</h2>
              <p>
                {t("{count} measures · revision {revision}", {
                  count: chart.definition.measures.length,
                  revision: chart.revision,
                })}
              </p>
            </div>
            <footer>
              <time dateTime={chart.updated_at}>
                {t("Updated {date}", { date: formatDateTime(chart.updated_at, locale) })}
              </time>
              <ArrowRight size={17} />
            </footer>
          </Link>
        ))}
      </section>
      {!charts.isPending && values.length === 0 && (
        <div className="empty-state">
          <BarChart3 />
          <strong>{t(search ? "No matching charts" : "No charts yet")}</strong>
          <p>
            {search
              ? t("Try a different name.")
              : t("Create a visual answer from a published dataset.")}
          </p>
        </div>
      )}
      {charts.hasNextPage && (
        <button
          className="button secondary load-more"
          onClick={() => void charts.fetchNextPage()}
          disabled={charts.isFetchingNextPage}
          type="button"
        >
          {t(charts.isFetchingNextPage ? "Loading…" : "Load more")}
        </button>
      )}
    </div>
  );
}
