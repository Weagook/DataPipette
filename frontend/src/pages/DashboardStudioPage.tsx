import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  BarChart3,
  Check,
  ChevronsDownUp,
  ChevronsUpDown,
  Eye,
  GripVertical,
  LayoutDashboard,
  LoaderCircle,
  Lock,
  Maximize2,
  Minimize2,
  Plus,
  Save,
  Search,
  Share2,
  Trash2,
  UserRound,
  UsersRound,
} from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useState, type DragEvent } from "react";
import {
  Link,
  Navigate,
  useNavigate,
  useOutletContext,
  useParams,
  useSearchParams,
} from "react-router-dom";

import { DashboardFilterEditor } from "../components/DashboardFilterEditor";
import { InlineError } from "../components/Status";
import {
  createDashboard,
  createDashboardGrant,
  getDashboardForEdit,
  listCharts,
  listDashboardGrants,
  listGroups,
  listUsers,
  problemMessage,
  queryChart,
  replaceDashboard,
  revokeDashboardGrant,
  type Chart,
  type CurrentUser,
  type DashboardDefinition,
  type DashboardGrant,
  type DashboardItem,
  type GrantPrincipalType,
} from "../lib/api";
import {
  DASHBOARD_COLUMNS,
  addChart,
  compactLayout,
  emptyDashboardDefinition,
  itemsOf,
  removeItem,
  updateItem,
} from "../lib/dashboardLayout";
import { formatDateTime } from "../lib/format";
import { randomUuid } from "../lib/id";
import { useI18n } from "../lib/i18n";
import { queryKeys } from "../lib/query";

const ChartView = lazy(() =>
  import("../components/ChartView").then((module) => ({ default: module.ChartView })),
);

const typeLabels = {
  bar: "Bar",
  line: "Line",
  pie: "Pie",
  donut: "Donut",
  kpi: "KPI",
} as const;

export function DashboardStudioPage() {
  const user = useOutletContext<CurrentUser>();
  const { dashboardId } = useParams();
  if (user.role === "viewer") return <Navigate to="/overview" replace />;
  return dashboardId ? (
    <ExistingDashboardStudio dashboardId={dashboardId} user={user} />
  ) : (
    <NewDashboardStudio user={user} />
  );
}

function NewDashboardStudio({ user }: { user: CurrentUser }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [definition, setDefinition] = useState<DashboardDefinition>(
    emptyDashboardDefinition(),
  );
  const create = useMutation({
    mutationFn: () => createDashboard(name.trim(), definition),
    onSuccess: async ({ dashboard, etag }) => {
      queryClient.setQueryData(queryKeys.dashboardEdit(dashboard.id), {
        dashboard,
        etag,
      });
      await queryClient.invalidateQueries({ queryKey: queryKeys.dashboards });
      await navigate(`/dashboards/${dashboard.id}/edit`, { replace: true });
    },
  });

  return (
    <DashboardEditor
      user={user}
      name={name}
      definition={definition}
      onNameChange={setName}
      onDefinitionChange={setDefinition}
      onSave={() => create.mutate()}
      savePending={create.isPending}
      saveError={create.error}
      dirty
    />
  );
}

function ExistingDashboardStudio({
  dashboardId,
  user,
}: {
  dashboardId: string;
  user: CurrentUser;
}) {
  const queryClient = useQueryClient();
  const dashboardQuery = useQuery({
    queryKey: queryKeys.dashboardEdit(dashboardId),
    queryFn: () => getDashboardForEdit(dashboardId),
  });
  const [name, setName] = useState("");
  const [definition, setDefinition] = useState<DashboardDefinition>(
    emptyDashboardDefinition(),
  );
  const [loadedRevision, setLoadedRevision] = useState<number | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const pendingChartId = searchParams.get("add");

  useEffect(() => {
    // A chart created from this dashboard comes back here to be placed on it.
    if (!pendingChartId || loadedRevision === null) return;
    setDefinition((current) =>
      itemsOf(current).some((item) => item.chart_id === pendingChartId)
        ? current
        : addChart(current, pendingChartId, randomUuid()),
    );
    setSearchParams({}, { replace: true });
  }, [loadedRevision, pendingChartId, setSearchParams]);

  useEffect(() => {
    if (
      dashboardQuery.data &&
      dashboardQuery.data.dashboard.revision !== loadedRevision
    ) {
      setName(dashboardQuery.data.dashboard.name);
      setDefinition(dashboardQuery.data.dashboard.definition);
      setLoadedRevision(dashboardQuery.data.dashboard.revision);
    }
  }, [dashboardQuery.data, loadedRevision]);

  const replace = useMutation({
    mutationFn: () => {
      if (!dashboardQuery.data) throw new Error("Dashboard is not loaded.");
      return replaceDashboard(
        dashboardId,
        name.trim(),
        definition,
        dashboardQuery.data.etag,
      );
    },
    onSuccess: async (value) => {
      setLoadedRevision(value.dashboard.revision);
      queryClient.setQueryData(queryKeys.dashboardEdit(dashboardId), value);
      queryClient.setQueryData(queryKeys.dashboard(dashboardId), value.dashboard);
      await queryClient.invalidateQueries({ queryKey: queryKeys.dashboards });
    },
  });

  if (dashboardQuery.isPending)
    return <div className="studio-panel skeleton tall" />;
  if (dashboardQuery.isError)
    return (
      <InlineError
        message={problemMessage(dashboardQuery.error)}
        onRetry={() => void dashboardQuery.refetch()}
      />
    );

  const saved = dashboardQuery.data.dashboard;
  const dirty =
    name.trim() !== saved.name ||
    JSON.stringify(definition) !== JSON.stringify(saved.definition);

  return (
    <DashboardEditor
      user={user}
      dashboardId={dashboardId}
      ownerId={saved.owner_id}
      name={name}
      definition={definition}
      updatedAt={saved.updated_at}
      revision={saved.revision}
      onNameChange={setName}
      onDefinitionChange={setDefinition}
      onSave={() => replace.mutate()}
      savePending={replace.isPending}
      saveError={replace.error}
      dirty={dirty}
    />
  );
}

interface DashboardEditorProps {
  user: CurrentUser;
  dashboardId?: string;
  ownerId?: string;
  name: string;
  definition: DashboardDefinition;
  revision?: number;
  updatedAt?: string;
  onNameChange: (name: string) => void;
  onDefinitionChange: (definition: DashboardDefinition) => void;
  onSave: () => void;
  savePending: boolean;
  saveError: unknown;
  dirty: boolean;
}

function DashboardEditor(props: DashboardEditorProps) {
  const { locale, t } = useI18n();
  const [chartSearch, setChartSearch] = useState("");
  const [layoutNotice, setLayoutNotice] = useState("");
  const charts = useQuery({
    queryKey: queryKeys.dashboardCharts,
    queryFn: () => listCharts(),
  });
  const ownerId = props.ownerId ?? props.user.id;
  const availableCharts = useMemo(
    () =>
      (charts.data?.data ?? []).filter(
        (chart) =>
          chart.owner_id === ownerId &&
          chart.name.toLocaleLowerCase().includes(chartSearch.toLocaleLowerCase()),
      ),
    [charts.data, chartSearch, ownerId],
  );
  const chartById = useMemo(
    () => new Map((charts.data?.data ?? []).map((chart) => [chart.id, chart])),
    [charts.data],
  );
  const itemChartIds = new Set(itemsOf(props.definition).map((item) => item.chart_id));

  function changeItem(
    item: DashboardItem,
    change: Partial<Pick<DashboardItem, "x" | "y" | "width" | "height">>,
  ) {
    const next = updateItem(props.definition, item.id, change);
    if (!next) {
      setLayoutNotice(t("That position would overlap another tile or leave the 12-column grid."));
      return;
    }
    setLayoutNotice("");
    props.onDefinitionChange(next);
  }

  function add(chartId: string) {
    if (itemsOf(props.definition).length >= 50) {
      setLayoutNotice(t("A dashboard can contain at most 50 visualizations."));
      return;
    }
    props.onDefinitionChange(addChart(props.definition, chartId, randomUuid()));
    setLayoutNotice("");
  }

  function dropItem(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    const itemId = event.dataTransfer.getData("text/datapipette-item");
    const item = itemsOf(props.definition).find((value) => value.id === itemId);
    if (!item) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = Math.max(
      0,
      Math.min(
        DASHBOARD_COLUMNS - item.width,
        Math.floor(((event.clientX - bounds.left) / bounds.width) * DASHBOARD_COLUMNS),
      ),
    );
    const y = Math.max(0, Math.floor((event.clientY - bounds.top) / 74));
    changeItem(item, { x, y });
  }

  const canSave = props.name.trim().length > 0 && props.dirty && !props.savePending;

  return (
    <div className="dashboard-studio page-stack">
      <header className="dashboard-studio-bar">
        <Link className="back-link" to="/dashboards">
          <ArrowLeft size={15} /> {t("Dashboards")}
        </Link>
        <input
          className="dashboard-name"
          value={props.name}
          onChange={(event) => props.onNameChange(event.target.value)}
          placeholder={t("Dashboard name")}
          aria-label={t("Dashboard name")}
          maxLength={100}
        />
        <span className={`dashboard-status ${props.dirty ? "unsaved" : "saved"}`}>
          {props.dirty ? <span className="status-dot" /> : <Check size={14} />}
          {t(!props.dashboardId ? "Not created yet" : props.dirty ? "Unsaved changes" : "Saved")}
          {props.updatedAt && !props.dirty && ` · ${formatDateTime(props.updatedAt, locale)}`}
        </span>
        {props.dashboardId && (
          <Link className="button secondary small" to={`/dashboards/${props.dashboardId}`}>
            <Eye size={15} /> {t("Open dashboard")}
          </Link>
        )}
        <button className="button primary small" onClick={props.onSave} disabled={!canSave} type="button">
          {props.savePending ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />}
          {t(props.dashboardId ? "Save" : "Create dashboard")}
        </button>
      </header>

      {props.saveError != null && <InlineError message={problemMessage(props.saveError)} />}
      {layoutNotice && <div className="layout-notice" role="status">{layoutNotice}</div>}

      <div className="dashboard-studio-workspace">
        <section className="layout-workbench">
          <header>
            <div><span><strong>{t("Dashboard")}</strong><small>{t("Drag a chart by its title. Hover a chart to move or resize it with buttons.")}</small></span></div>
            <button className="button secondary small" type="button" onClick={() => props.onDefinitionChange(compactLayout(props.definition))} disabled={itemsOf(props.definition).length === 0}>
              <Minimize2 size={15} /> {t("Close gaps")}
            </button>
          </header>
          <div
            className="layout-canvas"
            onDragOver={(event) => event.preventDefault()}
            onDrop={dropItem}
            aria-label={t("Dashboard layout canvas")}
          >
            <div className="layout-columns" aria-hidden="true">{Array.from({ length: 12 }, (_, index) => <i key={index} />)}</div>
            {itemsOf(props.definition).map((item) => (
              <LayoutTile
                key={item.id}
                item={item}
                chart={chartById.get(item.chart_id)}
                onChange={(change) => changeItem(item, change)}
                onRemove={() => props.onDefinitionChange(removeItem(props.definition, item.id))}
              />
            ))}
            {itemsOf(props.definition).length === 0 && (
              <div className="layout-empty"><LayoutDashboard size={30} /><strong>{t("Add your first chart")}</strong><p>{t("Press + next to a chart on the right, or create a new one.")}</p></div>
            )}
          </div>
        </section>

        <aside className="chart-palette">
          <header><span><strong>{t("Your charts")}</strong><small>{t("Press + to place a chart on the dashboard.")}</small></span></header>
          <label className="search-field"><Search size={16} /><span className="sr-only">{t("Search chart palette")}</span><input value={chartSearch} onChange={(event) => setChartSearch(event.target.value)} placeholder={t("Search saved charts")} /></label>
          {charts.isError && <InlineError message={problemMessage(charts.error)} />}
          <div className="palette-list">
            {charts.isPending && [0, 1, 2].map((value) => <div className="palette-card skeleton" key={value} />)}
            {availableCharts.map((chart) => (
              <article className="palette-card" key={chart.id}>
                <span className="palette-chart-icon"><BarChart3 size={17} /></span>
                <span><strong>{chart.name}</strong><small>{t(typeLabels[chart.definition.chart_type])}</small></span>
                <button className="icon-button bordered" type="button" onClick={() => add(chart.id)} disabled={itemChartIds.has(chart.id)} aria-label={t("{action} {name}", { action: t(itemChartIds.has(chart.id) ? "Added" : "Add"), name: chart.name })}>
                  {itemChartIds.has(chart.id) ? <Check size={16} /> : <Plus size={16} />}
                </button>
              </article>
            ))}
            {!charts.isPending && availableCharts.length === 0 && <div className="compact-empty"><BarChart3 /> {t(chartSearch ? "No matching charts" : "You have no charts yet")}</div>}
          </div>
          {props.dashboardId ? (
            <Link
              className="button secondary wide"
              to={`/charts/new?returnTo=${encodeURIComponent(`/dashboards/${props.dashboardId}/edit`)}`}
            >
              <Plus size={15} /> {t("New chart")}
            </Link>
          ) : (
            <a className="button secondary wide" href="/charts/new" target="_blank" rel="noreferrer">
              <Plus size={15} /> {t("New chart")}
            </a>
          )}
        </aside>
      </div>

      <DashboardFilterEditor definition={props.definition} chartById={chartById} onChange={props.onDefinitionChange} />

      {props.dashboardId ? (
        <SharingPanel dashboardId={props.dashboardId} user={props.user} />
      ) : (
        <section className="sharing-locked"><Lock size={18} /><div><strong>{t("Sharing unlocks after creation")}</strong><p>{t("Save the private dashboard first, then grant view access to a person or group.")}</p></div></section>
      )}
    </div>
  );
}

function LayoutTile({
  item,
  chart,
  onChange,
  onRemove,
}: {
  item: DashboardItem;
  chart?: Chart;
  onChange: (change: Partial<Pick<DashboardItem, "x" | "y" | "width" | "height">>) => void;
  onRemove: () => void;
}) {
  const { t } = useI18n();
  return (
    <article
      className="layout-tile"
      draggable
      onDragStart={(event) => {
        event.dataTransfer.setData("text/datapipette-item", item.id);
        event.dataTransfer.effectAllowed = "move";
      }}
      style={{ gridColumn: `${item.x + 1} / span ${item.width}`, gridRow: `${item.y + 1} / span ${item.height}` }}
    >
      <header><GripVertical size={16} /><span><strong>{chart?.name ?? t("Unavailable chart")}</strong><small>{chart ? t(typeLabels[chart.definition.chart_type]) : shortId(item.chart_id)}</small></span><button className="icon-button" type="button" onClick={onRemove} aria-label={t("Remove {name}", { name: chart?.name ?? t("chart") })}><Trash2 size={15} /></button></header>
      <TilePreview chart={chart} />
      <footer>
        <div className="tile-control-group" aria-label={t("Move {name}", { name: chart?.name ?? t("chart") })}>
          <button type="button" onClick={() => onChange({ x: item.x - 1 })} aria-label={t("Move left")}><ArrowLeft /></button>
          <button type="button" onClick={() => onChange({ y: Math.max(0, item.y - 1) })} aria-label={t("Move up")}><ArrowUp /></button>
          <button type="button" onClick={() => onChange({ y: item.y + 1 })} aria-label={t("Move down")}><ArrowDown /></button>
          <button type="button" onClick={() => onChange({ x: item.x + 1 })} aria-label={t("Move right")}><ArrowRight /></button>
        </div>
        <div className="tile-control-group size" aria-label={t("Resize {name}", { name: chart?.name ?? t("chart") })}>
          <button type="button" onClick={() => onChange({ width: Math.max(1, item.width - 1) })} aria-label={t("Decrease width")}><Minimize2 /></button>
          <span>{item.width}×{item.height}</span>
          <button type="button" onClick={() => onChange({ width: Math.min(12, item.width + 1) })} aria-label={t("Increase width")}><Maximize2 /></button>
          <button type="button" onClick={() => onChange({ height: Math.max(1, item.height - 1) })} aria-label={t("Decrease height")}><ChevronsDownUp /></button>
          <button type="button" onClick={() => onChange({ height: Math.min(100, item.height + 1) })} aria-label={t("Increase height")}><ChevronsUpDown /></button>
        </div>
      </footer>
    </article>
  );
}

function TilePreview({ chart }: { chart?: Chart }) {
  const { t } = useI18n();
  const preview = useQuery({
    queryKey: queryKeys.chartPreview(chart?.id ?? "none", chart?.revision ?? 0),
    queryFn: () => queryChart(chart!.id),
    enabled: Boolean(chart),
  });
  if (!chart || preview.isError)
    return <div className="tile-placeholder"><BarChart3 /><span>{t("Chart unavailable")}</span></div>;
  if (!preview.data) return <div className="tile-placeholder skeleton" />;
  if (preview.data.data.rows.length === 0)
    return <div className="tile-placeholder"><BarChart3 /><span>{t("No data")}</span></div>;
  return (
    <div className="tile-chart">
      <Suspense fallback={<div className="tile-placeholder skeleton" />}>
        <ChartView result={preview.data} />
      </Suspense>
    </div>
  );
}

function SharingPanel({ dashboardId, user }: { dashboardId: string; user: CurrentUser }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [principalType, setPrincipalType] = useState<GrantPrincipalType>("user");
  const [principalId, setPrincipalId] = useState("");
  const grants = useQuery({ queryKey: queryKeys.dashboardGrants(dashboardId), queryFn: () => listDashboardGrants(dashboardId) });
  const users = useQuery({ queryKey: queryKeys.users, queryFn: listUsers, enabled: user.role === "admin" });
  const groups = useQuery({ queryKey: queryKeys.groups, queryFn: listGroups, enabled: user.role === "admin" });
  const create = useMutation({
    mutationFn: () => createDashboardGrant(dashboardId, principalType, principalId),
    onSuccess: async () => {
      setPrincipalId("");
      await queryClient.invalidateQueries({ queryKey: queryKeys.dashboardGrants(dashboardId) });
    },
  });
  const revoke = useMutation({
    mutationFn: (grantId: string) => revokeDashboardGrant(dashboardId, grantId),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: queryKeys.dashboardGrants(dashboardId) }),
  });
  const directory = principalType === "user" ? users.data : groups.data;
  const names = new Map<string, string>([
    ...(users.data ?? []).map((value) => [value.id, `${value.display_name} · ${value.email}`] as const),
    ...(groups.data ?? []).map((value) => [value.id, value.name] as const),
  ]);

  return (
    <section className="sharing-panel">
      <header><div><span><strong>{t("Who can view")}</strong><small>{t("People you add here see the saved dashboard, but not the underlying rows.")}</small></span></div><span className="private-pill"><Lock size={13} /> {t("Private by default")}</span></header>
      <div className="sharing-grid">
        <form onSubmit={(event) => { event.preventDefault(); create.mutate(); }}>
          <label><span>{t("Recipient type")}</span><select value={principalType} onChange={(event) => { setPrincipalType(event.target.value as GrantPrincipalType); setPrincipalId(""); }}><option value="user">{t("Person")}</option><option value="group">{t("Group")}</option></select></label>
          <label><span>{t(principalType === "user" ? "Person" : "Group")}</span>
            {user.role === "admin" ? (
              <select value={principalId} onChange={(event) => setPrincipalId(event.target.value)}><option value="">{t("Select a recipient")}</option>{directory?.filter((value) => principalType === "group" || ("status" in value && value.status === "active")).map((value) => <option key={value.id} value={value.id}>{"display_name" in value ? `${value.display_name} · ${value.email}` : value.name}</option>)}</select>
            ) : (
              <input value={principalId} onChange={(event) => setPrincipalId(event.target.value)} placeholder={`${principalType} UUID`} />
            )}
          </label>
          <button className="button primary" type="submit" disabled={!principalId || create.isPending}>{create.isPending ? <LoaderCircle className="spin" size={15} /> : <Share2 size={15} />} {t("Grant view access")}</button>
          {create.isError && <InlineError message={problemMessage(create.error)} />}
        </form>
        <div className="grant-list">
          <strong>{t("Who can view")}</strong>
          {grants.isError && <InlineError message={problemMessage(grants.error)} />}
          {grants.data?.map((grant) => <GrantRow key={grant.id} grant={grant} name={names.get(grant.principal_id)} onRevoke={() => revoke.mutate(grant.id)} pending={revoke.isPending} />)}
          {!grants.isPending && grants.data?.length === 0 && <div className="compact-empty"><Lock /> {t("Owner and administrators only")}</div>}
        </div>
      </div>
    </section>
  );
}

function GrantRow({ grant, name, onRevoke, pending }: { grant: DashboardGrant; name?: string; onRevoke: () => void; pending: boolean }) {
  const { t } = useI18n();
  const Icon = grant.principal_type === "group" ? UsersRound : UserRound;
  return <article className="grant-row"><span><Icon size={16} /></span><div><strong>{name ?? `${grant.principal_type} ${shortId(grant.principal_id)}`}</strong><small>{t("Can view saved aggregate items")}</small></div><button className="button secondary small" type="button" onClick={onRevoke} disabled={pending}>{t("Revoke")}</button></article>;
}

function shortId(value: string): string {
  return value.length > 10 ? `${value.slice(0, 8)}…` : value;
}
