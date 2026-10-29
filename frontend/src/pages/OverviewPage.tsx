import { useQueries, useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowUpRight,
  BarChart3,
  Check,
  Database,
  LayoutDashboard,
  PlugZap,
} from "lucide-react";
import { Link, useOutletContext } from "react-router-dom";

import { InlineError } from "../components/Status";
import {
  getDatasetFreshness,
  listCharts,
  listConnections,
  listDashboards,
  listDatasets,
  problemMessage,
  type CurrentUser,
} from "../lib/api";
import { formatDateTime, relativeTime } from "../lib/format";
import { useI18n } from "../lib/i18n";
import { queryKeys } from "../lib/query";

const ATTENTION_DATASETS = 20;
type Recent = { id: string; name: string; updated_at: string; href: string; detail: string };

export function OverviewPage() {
  const { t } = useI18n();
  const user = useOutletContext<CurrentUser | undefined>();
  const author = Boolean(user && user.role !== "viewer");
  const dashboards = useQuery({ queryKey: queryKeys.dashboardOverview, queryFn: () => listDashboards() });
  const datasets = useQuery({ queryKey: ["datasets", "overview"], queryFn: () => listDatasets(), enabled: author });
  const charts = useQuery({ queryKey: ["charts", "overview"], queryFn: () => listCharts(), enabled: author });
  const connections = useQuery({ queryKey: queryKeys.connections, queryFn: () => listConnections(), enabled: author });

  return (
    <div className="page-stack home">
      <header className="page-heading overview-heading">
        <div>
          <h1>{t("Overview")}</h1>
          <p>{t("Open a dashboard, or prepare new data and charts.")}</p>
        </div>
      </header>

      {author && (
        <GettingStarted
          isAdmin={user?.role === "admin"}
          ready={!datasets.isPending && !charts.isPending && !connections.isPending && !dashboards.isPending}
          hasConnection={(connections.data?.data.length ?? 0) > 0}
          hasLoadedDataset={(datasets.data?.data ?? []).some((dataset) => dataset.active_snapshot_id !== null)}
          hasChart={(charts.data?.data.length ?? 0) > 0}
          hasDashboard={(dashboards.data?.data ?? []).some((dashboard) => (dashboard.definition.items?.length ?? 0) > 0)}
        />
      )}

      {author && datasets.data && <NeedsAttention datasets={datasets.data.data.filter((item) => item.active_snapshot_id !== null).slice(0, ATTENTION_DATASETS)} />}

      {dashboards.isError && <InlineError message={problemMessage(dashboards.error)} onRetry={() => void dashboards.refetch()} />}
      <div className={`home-recent${author ? "" : " single"}`}>
        <RecentList
          title={t("Dashboards")}
          icon={<LayoutDashboard size={17} />}
          all="/dashboards"
          pending={dashboards.isPending}
          empty={t("No dashboards yet")}
          items={(dashboards.data?.data ?? []).map((item) => ({
            id: item.id,
            name: item.name,
            updated_at: item.updated_at,
            href: `/dashboards/${item.id}`,
            detail: t("{count} visualizations", { count: item.definition.items?.length ?? 0 }),
          }))}
        />
        {author && (
          <>
            <RecentList
              title={t("Datasets")}
              icon={<Database size={17} />}
              all="/datasets"
              pending={datasets.isPending}
              empty={t("No datasets yet")}
              items={(datasets.data?.data ?? []).map((item) => ({
                id: item.id,
                name: item.name,
                updated_at: item.updated_at,
                href: item.active_snapshot_id ? `/datasets/${item.id}/table` : `/datasets/${item.id}/draft`,
                detail: t(item.active_snapshot_id ? "Data loaded" : "Not loaded yet"),
              }))}
            />
            <RecentList
              title={t("Charts")}
              icon={<BarChart3 size={17} />}
              all="/charts"
              pending={charts.isPending}
              empty={t("You have no charts yet")}
              items={(charts.data?.data ?? []).map((item) => ({
                id: item.id,
                name: item.name,
                updated_at: item.updated_at,
                href: `/charts/${item.id}`,
                detail: "",
              }))}
            />
          </>
        )}
      </div>
    </div>
  );
}

function GettingStarted({
  isAdmin,
  ready,
  hasConnection,
  hasLoadedDataset,
  hasChart,
  hasDashboard,
}: {
  isAdmin: boolean;
  ready: boolean;
  hasConnection: boolean;
  hasLoadedDataset: boolean;
  hasChart: boolean;
  hasDashboard: boolean;
}) {
  const { t } = useI18n();
  const steps = [
    {
      done: hasConnection,
      icon: <PlugZap size={17} />,
      title: "Connect a database",
      detail: isAdmin ? "Tell DataPipette where your PostgreSQL data lives." : "Ask an administrator to connect a database for you.",
      href: isAdmin ? "/connections/new" : null,
    },
    { done: hasLoadedDataset, icon: <Database size={17} />, title: "Prepare a dataset", detail: "Pick tables, link them, and load the data.", href: "/datasets/new" },
    { done: hasChart, icon: <BarChart3 size={17} />, title: "Build a chart", detail: "Show the data as bars, a line, a pie, or one number.", href: "/charts/new" },
    { done: hasDashboard, icon: <LayoutDashboard size={17} />, title: "Put charts on a dashboard", detail: "Collect charts on one page and share it.", href: "/dashboards/new" },
  ];
  if (!ready || steps.every((step) => step.done)) return null;
  const next = steps.findIndex((step) => !step.done);
  return (
    <section className="home-steps" aria-label={t("Getting started")}>
      <h2>{t("Getting started")}</h2>
      <ol>
        {steps.map((step, index) => (
          <li key={step.title} className={step.done ? "done" : index === next ? "next" : ""}>
            <span className="home-step-icon">{step.done ? <Check size={17} /> : step.icon}</span>
            <span>
              <strong>{t(step.title)}</strong>
              <small>{t(step.detail)}</small>
            </span>
            {!step.done && index === next && step.href && (
              <Link className="button primary small" to={step.href}>{t("Start")}</Link>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

function NeedsAttention({ datasets }: { datasets: { id: string; name: string }[] }) {
  const { locale, t } = useI18n();
  const freshness = useQueries({
    queries: datasets.map((dataset) => ({
      queryKey: queryKeys.datasetFreshness(dataset.id),
      queryFn: () => getDatasetFreshness(dataset.id),
    })),
  });
  const problems = datasets
    .map((dataset, index) => ({ dataset, state: freshness[index]?.data }))
    .filter(({ state }) => state && (state.data_state === "failed" || state.data_state === "stale"));
  if (!problems.length) return null;
  return (
    <section className="home-attention" aria-label={t("Needs attention")}>
      <h2><AlertTriangle size={17} /> {t("Needs attention")}</h2>
      <ul>
        {problems.map(({ dataset, state }) => (
          <li key={dataset.id}>
            <Link to={`/datasets/${dataset.id}/draft`}>{dataset.name}</Link>
            <span>
              {t(state!.data_state === "failed" ? "Load failed" : "Last load failed")}
              {state!.last_refresh_error_at && ` · ${formatDateTime(state!.last_refresh_error_at, locale)}`}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function RecentList({
  title,
  icon,
  all,
  pending,
  empty,
  items,
}: {
  title: string;
  icon: React.ReactNode;
  all: string;
  pending: boolean;
  empty: string;
  items: Recent[];
}) {
  const { locale, t } = useI18n();
  const recent = [...items].sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 5);
  return (
    <section className="home-list">
      <header>
        <h2>{icon} {title}</h2>
        <Link className="text-link" to={all}>{t("View all")} <ArrowUpRight size={14} /></Link>
      </header>
      {pending && <div className="home-list-skeleton skeleton" />}
      {!pending && recent.length === 0 && <p className="home-list-empty">{empty}</p>}
      <ul>
        {recent.map((item) => (
          <li key={item.id}>
            <Link to={item.href}>
              <strong>{item.name}</strong>
              <small>
                {item.detail ? `${item.detail} · ` : ""}
                {relativeTime(item.updated_at, locale)}
              </small>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
