import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  BarChart3,
  Check,
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
import { useEffect, useMemo, useState, type DragEvent } from "react";
import {
  Link,
  Navigate,
  useNavigate,
  useOutletContext,
  useParams,
} from "react-router-dom";

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
import { queryKeys } from "../lib/query";

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
      setLayoutNotice("That position would overlap another tile or leave the 12-column grid.");
      return;
    }
    setLayoutNotice("");
    props.onDefinitionChange(next);
  }

  function add(chartId: string) {
    if (itemsOf(props.definition).length >= 50) {
      setLayoutNotice("A dashboard can contain at most 50 visualizations.");
      return;
    }
    props.onDefinitionChange(addChart(props.definition, chartId, randomUuid()));
    setLayoutNotice("");
  }

  function dropItem(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    const itemId = event.dataTransfer.getData("text/datepipette-item");
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
      <header className="page-heading dashboard-studio-heading">
        <div>
          <Link className="back-link" to="/dashboards">
            <ArrowLeft size={16} /> All dashboards
          </Link>
          <span className="eyebrow">Private workspace</span>
          <h1>{props.dashboardId ? "Compose the dashboard" : "Create a dashboard"}</h1>
          <p>
            Arrange governed charts from multiple datasets. The saved 12-column layout is the same document every client reads.
          </p>
        </div>
        <div className="heading-actions">
          {props.dashboardId && (
            <Link className="button secondary" to={`/dashboards/${props.dashboardId}`}>
              <Eye size={16} /> Viewer preview
            </Link>
          )}
          <button className="button primary" onClick={props.onSave} disabled={!canSave} type="button">
            {props.savePending ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}
            {props.dashboardId ? "Save layout" : "Create dashboard"}
          </button>
        </div>
      </header>

      <div className="dashboard-studio-statusbar">
        <span className={props.dirty ? "unsaved" : "saved"}>
          {props.dirty ? <span className="status-dot" /> : <Check size={14} />}
          {props.dirty ? "Unsaved changes" : "Saved"}
        </span>
        <span><Lock size={14} /> Private until explicitly shared</span>
        {props.revision && <span>Revision {props.revision}</span>}
        {props.updatedAt && <span>Updated {formatDateTime(props.updatedAt)}</span>}
      </div>

      <section className="dashboard-studio-name">
        <label>
          <span>Dashboard name</span>
          <input
            value={props.name}
            onChange={(event) => props.onNameChange(event.target.value)}
            placeholder="Executive revenue overview"
            maxLength={100}
          />
        </label>
        <div><strong>{itemsOf(props.definition).length}</strong><span>visualizations</span></div>
        <div><strong>{new Set(itemsOf(props.definition).map((item) => chartById.get(item.chart_id)?.dataset_id)).size}</strong><span>datasets</span></div>
      </section>

      {props.saveError != null && <InlineError message={problemMessage(props.saveError)} />}
      {layoutNotice && <div className="layout-notice" role="status">{layoutNotice}</div>}

      <div className="dashboard-studio-workspace">
        <section className="layout-workbench">
          <header>
            <div><span className="step-number">01</span><span><strong>Layout canvas</strong><small>Drag a tile or use its exact movement and sizing controls.</small></span></div>
            <button className="button secondary small" type="button" onClick={() => props.onDefinitionChange(compactLayout(props.definition))} disabled={itemsOf(props.definition).length === 0}>
              <Minimize2 size={15} /> Compact
            </button>
          </header>
          <div
            className="layout-canvas"
            onDragOver={(event) => event.preventDefault()}
            onDrop={dropItem}
            aria-label="Dashboard layout canvas"
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
              <div className="layout-empty"><LayoutDashboard size={30} /><strong>Start with a saved chart</strong><p>Add one from the palette. New dashboards remain private.</p></div>
            )}
          </div>
        </section>

        <aside className="chart-palette">
          <header><span className="step-number">02</span><span><strong>Chart palette</strong><small>Only charts owned by this dashboard owner are eligible.</small></span></header>
          <label className="search-field"><Search size={16} /><span className="sr-only">Search chart palette</span><input value={chartSearch} onChange={(event) => setChartSearch(event.target.value)} placeholder="Search saved charts" /></label>
          {charts.isError && <InlineError message={problemMessage(charts.error)} />}
          <div className="palette-list">
            {charts.isPending && [0, 1, 2].map((value) => <div className="palette-card skeleton" key={value} />)}
            {availableCharts.map((chart) => (
              <article className="palette-card" key={chart.id}>
                <span className="palette-chart-icon"><BarChart3 size={17} /></span>
                <span><strong>{chart.name}</strong><small>{typeLabels[chart.definition.chart_type]} · dataset {shortId(chart.dataset_id)}</small></span>
                <button className="icon-button bordered" type="button" onClick={() => add(chart.id)} disabled={itemChartIds.has(chart.id)} aria-label={`${itemChartIds.has(chart.id) ? "Added" : "Add"} ${chart.name}`}>
                  {itemChartIds.has(chart.id) ? <Check size={16} /> : <Plus size={16} />}
                </button>
              </article>
            ))}
            {!charts.isPending && availableCharts.length === 0 && <div className="compact-empty"><BarChart3 /> No matching owned charts</div>}
          </div>
          <Link className="button secondary wide" to="/charts/new"><Plus size={15} /> Create another chart</Link>
        </aside>
      </div>

      {props.dashboardId ? (
        <SharingPanel dashboardId={props.dashboardId} user={props.user} />
      ) : (
        <section className="sharing-locked"><Lock size={18} /><div><strong>Sharing unlocks after creation</strong><p>Save the private dashboard first, then grant view access to a person or group.</p></div></section>
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
  return (
    <article
      className="layout-tile"
      draggable
      onDragStart={(event) => {
        event.dataTransfer.setData("text/datepipette-item", item.id);
        event.dataTransfer.effectAllowed = "move";
      }}
      style={{ gridColumn: `${item.x + 1} / span ${item.width}`, gridRow: `${item.y + 1} / span ${item.height}` }}
    >
      <header><GripVertical size={16} /><span><strong>{chart?.name ?? "Unavailable chart"}</strong><small>{chart ? typeLabels[chart.definition.chart_type] : shortId(item.chart_id)}</small></span><button className="icon-button" type="button" onClick={onRemove} aria-label={`Remove ${chart?.name ?? "chart"}`}><Trash2 size={15} /></button></header>
      <div className="tile-placeholder"><BarChart3 /><span>{chart?.definition.measures.length ?? 0} saved measure{chart?.definition.measures.length === 1 ? "" : "s"}</span></div>
      <footer>
        <div className="tile-control-group" aria-label={`Move ${chart?.name ?? "chart"}`}>
          <button type="button" onClick={() => onChange({ x: item.x - 1 })} aria-label="Move left"><ArrowLeft /></button>
          <button type="button" onClick={() => onChange({ y: Math.max(0, item.y - 1) })} aria-label="Move up"><ArrowUp /></button>
          <button type="button" onClick={() => onChange({ y: item.y + 1 })} aria-label="Move down"><ArrowDown /></button>
          <button type="button" onClick={() => onChange({ x: item.x + 1 })} aria-label="Move right"><ArrowRight /></button>
        </div>
        <div className="tile-control-group size" aria-label={`Resize ${chart?.name ?? "chart"}`}>
          <button type="button" onClick={() => onChange({ width: Math.max(1, item.width - 1) })} aria-label="Decrease width"><Minimize2 /></button>
          <span>{item.width}×{item.height}</span>
          <button type="button" onClick={() => onChange({ width: Math.min(12, item.width + 1) })} aria-label="Increase width"><Maximize2 /></button>
          <button type="button" onClick={() => onChange({ height: Math.max(1, item.height - 1) })} aria-label="Decrease height">H−</button>
          <button type="button" onClick={() => onChange({ height: Math.min(100, item.height + 1) })} aria-label="Increase height">H+</button>
        </div>
      </footer>
    </article>
  );
}

function SharingPanel({ dashboardId, user }: { dashboardId: string; user: CurrentUser }) {
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
      <header><div><span className="step-number">03</span><span><strong>Share view access</strong><small>Recipients can read only saved dashboard items; row data and chart authoring remain concealed.</small></span></div><span className="private-pill"><Lock size={13} /> Private by default</span></header>
      <div className="sharing-grid">
        <form onSubmit={(event) => { event.preventDefault(); create.mutate(); }}>
          <label><span>Recipient type</span><select value={principalType} onChange={(event) => { setPrincipalType(event.target.value as GrantPrincipalType); setPrincipalId(""); }}><option value="user">Person</option><option value="group">Group</option></select></label>
          <label><span>{principalType === "user" ? "Person" : "Group"}</span>
            {user.role === "admin" ? (
              <select value={principalId} onChange={(event) => setPrincipalId(event.target.value)}><option value="">Select a recipient</option>{directory?.filter((value) => principalType === "group" || ("status" in value && value.status === "active")).map((value) => <option key={value.id} value={value.id}>{"display_name" in value ? `${value.display_name} · ${value.email}` : value.name}</option>)}</select>
            ) : (
              <input value={principalId} onChange={(event) => setPrincipalId(event.target.value)} placeholder={`${principalType} UUID`} />
            )}
          </label>
          <button className="button primary" type="submit" disabled={!principalId || create.isPending}>{create.isPending ? <LoaderCircle className="spin" size={15} /> : <Share2 size={15} />} Grant view access</button>
          {create.isError && <InlineError message={problemMessage(create.error)} />}
        </form>
        <div className="grant-list">
          <strong>Who can view</strong>
          {grants.isError && <InlineError message={problemMessage(grants.error)} />}
          {grants.data?.map((grant) => <GrantRow key={grant.id} grant={grant} name={names.get(grant.principal_id)} onRevoke={() => revoke.mutate(grant.id)} pending={revoke.isPending} />)}
          {!grants.isPending && grants.data?.length === 0 && <div className="compact-empty"><Lock /> Owner and administrators only</div>}
        </div>
      </div>
    </section>
  );
}

function GrantRow({ grant, name, onRevoke, pending }: { grant: DashboardGrant; name?: string; onRevoke: () => void; pending: boolean }) {
  const Icon = grant.principal_type === "group" ? UsersRound : UserRound;
  return <article className="grant-row"><span><Icon size={16} /></span><div><strong>{name ?? `${grant.principal_type} ${shortId(grant.principal_id)}`}</strong><small>Can view saved aggregate items</small></div><button className="button secondary small" type="button" onClick={onRevoke} disabled={pending}>Revoke</button></article>;
}

function shortId(value: string): string {
  return value.length > 10 ? `${value.slice(0, 8)}…` : value;
}
