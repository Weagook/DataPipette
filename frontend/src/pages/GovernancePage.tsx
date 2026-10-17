import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Activity,
  ChevronDown,
  ChevronUp,
  Clock3,
  LoaderCircle,
  RotateCcw,
  Save,
  Search,
  ShieldCheck,
  SlidersHorizontal,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Navigate, useOutletContext } from "react-router-dom";

import { InlineError } from "../components/Status";
import {
  getQuerySafetyPolicy,
  listAuditEvents,
  problemMessage,
  replaceQuerySafetyPolicy,
  type AuditActorType,
  type AuditEvent,
  type AuditFilters,
  type AuditOutcome,
  type CurrentUser,
  type QuerySafetyPolicy,
  type QuerySafetyPolicyInput,
} from "../lib/api";
import { formatDateTime } from "../lib/format";
import { useI18n } from "../lib/i18n";
import { queryKeys } from "../lib/query";

type GovernanceTab = "policy" | "audit";
type PolicyField = keyof QuerySafetyPolicyInput;
type PolicyDraft = Record<PolicyField, string>;

interface FieldSpec {
  key: PolicyField;
  label: string;
  hint: string;
  min: number;
  max: number;
  step?: number | "any";
  exclusiveMin?: boolean;
}

interface FieldGroup {
  title: string;
  description: string;
  fields: FieldSpec[];
}

const POLICY_GROUPS: FieldGroup[] = [
  {
    title: "Planner warnings",
    description: "Warn or block before an expensive plan reaches execution.",
    fields: [
      field("estimated_rows_warning", "Estimated rows warning", "rows", 1, 1e12),
      field("estimated_rows_block", "Estimated rows block", "rows", 2, 1e12),
      field("total_cost_warning", "Planner cost warning", "cost units", 0, 1e12, "any", true),
      field("total_cost_block", "Planner cost block", "cost units", 0, 1e12, "any", true),
      field("join_count_warning", "Join warning", "joins", 1, 63),
      field("join_count_block", "Join block", "joins", 2, 64),
      field("sequential_scan_rows_warning", "Sequential scan warning", "rows", 1, 1e12),
      field("sort_rows_warning", "Sort warning", "rows", 1, 1e12),
      field("stale_statistics_days", "Stale statistics", "days", 1, 3650),
    ],
  },
  {
    title: "Dataset lifecycle",
    description: "Hard ceilings for previews and snapshot refreshes.",
    fields: [
      field("preview_statement_timeout_seconds", "Preview timeout", "seconds", 1, 60),
      field("preview_max_rows", "Preview rows", "rows", 1, 200),
      field("preview_max_response_bytes", "Preview response", "bytes", 65_536, 1_048_576),
      field("refresh_statement_timeout_seconds", "Refresh timeout", "seconds", 1, 86_400),
      field("refresh_max_rows", "Refresh row limit", "rows", 1, 1e9),
    ],
  },
  {
    title: "Interactive queries",
    description: "DuckDB limits applied while people explore snapshots.",
    fields: [
      field("interactive_query_timeout_seconds", "Query timeout", "seconds", 0, 60, "any", true),
      field("interactive_query_memory_limit_mb", "Memory", "MB", 64, 4096),
      field("interactive_query_threads", "Threads", "threads", 1, 16),
      field("interactive_query_concurrency", "Concurrency", "queries", 1, 64),
      field("interactive_query_temp_limit_mb", "Temporary storage", "MB", 64, 8192),
      field("interactive_query_max_response_bytes", "Response size", "bytes", 65_536, 16_777_216),
    ],
  },
  {
    title: "Exports and jobs",
    description: "Resource envelopes for background files and workers.",
    fields: [
      field("export_timeout_seconds", "Export timeout", "seconds", 1, 86_400),
      field("export_memory_limit_mb", "Export memory", "MB", 64, 4096),
      field("export_threads", "Export threads", "threads", 1, 16),
      field("export_temp_limit_mb", "Export temporary storage", "MB", 64, 32_768),
      field("export_max_rows", "Export rows", "rows", 1, 1e7),
      field("export_max_bytes", "Export file size", "bytes", 65_536, 10_737_418_240),
      field("background_job_concurrency", "Background concurrency", "jobs", 1, 64),
    ],
  },
];

const POLICY_FIELDS = POLICY_GROUPS.flatMap((group) => group.fields);

export function GovernancePage() {
  const user = useOutletContext<CurrentUser>();
  if (user.role !== "admin") return <Navigate to="/overview" replace />;
  return <GovernanceAdmin />;
}

function GovernanceAdmin() {
  const { t } = useI18n();
  const [tab, setTab] = useState<GovernanceTab>("policy");
  return (
    <div className="page-stack governance-page">
      <header className="page-heading">
        <div>
          <span className="eyebrow">{t("Operational guardrails")}</span>
          <h1>{t("Governance")}</h1>
          <p>{t("Control execution limits and trace sensitive activity without leaving the analytics workspace.")}</p>
        </div>
        <span className="trust-pill"><ShieldCheck size={16} /> {t("Admin-only control plane")}</span>
      </header>
      <nav className="access-tabs" aria-label={t("Governance areas")}>
        <button className={tab === "policy" ? "active" : ""} onClick={() => setTab("policy")} type="button"><SlidersHorizontal size={16} /> {t("Query safety")}</button>
        <button className={tab === "audit" ? "active" : ""} onClick={() => setTab("audit")} type="button"><Activity size={16} /> {t("Audit history")}</button>
      </nav>
      {tab === "policy" ? <PolicyEditor /> : <AuditExplorer />}
    </div>
  );
}

function PolicyEditor() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const policyQuery = useQuery({ queryKey: queryKeys.querySafetyPolicy, queryFn: getQuerySafetyPolicy });
  const [draft, setDraft] = useState<PolicyDraft | null>(null);
  useEffect(() => {
    if (policyQuery.data) setDraft(toDraft(policyQuery.data.policy));
  }, [policyQuery.data?.etag]);

  const validation = useMemo(() => draft ? validatePolicy(draft) : {}, [draft]);
  const dirty = Boolean(draft && policyQuery.data && POLICY_FIELDS.some(({ key }) => Number(draft[key]) !== policyQuery.data.policy[key]));
  const save = useMutation({
    mutationFn: () => replaceQuerySafetyPolicy(toPolicyInput(draft!), policyQuery.data!.etag),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.querySafetyPolicy, data);
      setDraft(toDraft(data.policy));
    },
  });

  if (policyQuery.isPending) return <LoadingSurface label={t("Loading safety policy")} />;
  if (policyQuery.isError) return <InlineError message={problemMessage(policyQuery.error)} onRetry={() => void policyQuery.refetch()} />;
  if (!draft) return <LoadingSurface label={t("Preparing safety policy")} />;
  const policy = policyQuery.data.policy;

  return (
    <section className="governance-surface policy-surface">
      <header className="governance-surface-heading">
        <div><span className="panel-icon"><ShieldCheck size={18} /></span><span><strong>{t("Query Safety policy")}</strong><small>{t("One versioned policy protects previews, refreshes, queries, exports, and workers.")}</small></span></div>
        <div className="policy-revision"><small>{t("Revision")}</small><strong>{policy.revision}</strong><span>{policy.updated_at ? t("Updated {date}", { date: formatDateTime(policy.updated_at) }) : t("Default policy")}</span></div>
      </header>
      <div className="policy-notice"><Clock3 size={17} /><div><strong>{t("Optimistic concurrency is active")}</strong><p>{t("Saving replaces the complete policy only if nobody changed revision {revision} in the meantime.", { revision: policy.revision })}</p></div></div>
      {save.isError && <InlineError message={problemMessage(save.error)} onRetry={() => void policyQuery.refetch()} />}
      {save.isSuccess && !dirty && <div className="governance-success"><ShieldCheck size={16} /> {t("Policy revision {revision} is active.", { revision: save.data.policy.revision })}</div>}
      <form onSubmit={(event) => { event.preventDefault(); if (dirty && Object.keys(validation).length === 0) save.mutate(); }}>
        <div className="policy-groups">
          {POLICY_GROUPS.map((group) => (
            <fieldset className="policy-group" key={group.title}>
              <legend>{t(group.title)}<small>{t(group.description)}</small></legend>
              <div className="policy-field-grid">
                {group.fields.map((spec) => (
                  <label className={validation[spec.key] ? "invalid" : ""} key={spec.key}>
                    <span>{t(spec.label)}</span>
                    <div><input aria-label={t(spec.label)} type="number" min={spec.min} max={spec.max} step={spec.step ?? 1} value={draft[spec.key]} onChange={(event) => setDraft({ ...draft, [spec.key]: event.target.value })} /><small>{t(spec.hint)}</small></div>
                    {validation[spec.key] && <em>{t(validation[spec.key]!)}</em>}
                  </label>
                ))}
              </div>
            </fieldset>
          ))}
        </div>
        <footer className="policy-actions">
          <span>{t(dirty ? (Object.keys(validation).length ? "Fix validation before saving" : "Unsaved policy changes") : "Policy is synchronized")}</span>
          <button className="button secondary" type="button" onClick={() => setDraft(toDraft(policy))} disabled={!dirty || save.isPending}><RotateCcw size={16} /> {t("Reset")}</button>
          <button className="button primary" type="submit" disabled={!dirty || Object.keys(validation).length > 0 || save.isPending}>{save.isPending ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />} {t("Save complete policy")}</button>
        </footer>
      </form>
    </section>
  );
}

function AuditExplorer() {
  const { t } = useI18n();
  const [draft, setDraft] = useState<AuditFilterDraft>(EMPTY_FILTERS);
  const [filters, setFilters] = useState<AuditFilters>({});
  const [filterError, setFilterError] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const events = useInfiniteQuery({
    queryKey: queryKeys.auditEvents(filters),
    queryFn: ({ pageParam }) => listAuditEvents(filters, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.meta.next_cursor ?? undefined,
  });
  const rows = events.data?.pages.flatMap((page) => page.data) ?? [];

  const applyFilters = () => {
    const next = auditFilters(draft);
    if (next.occurredFrom && next.occurredTo && next.occurredFrom > next.occurredTo) {
      setFilterError(t("The start time must be earlier than the end time."));
      return;
    }
    setFilterError("");
    setExpanded(null);
    setFilters(next);
  };
  const clearFilters = () => { setDraft(EMPTY_FILTERS); setFilters({}); setFilterError(""); setExpanded(null); };

  return (
    <section className="governance-surface audit-surface">
      <header className="governance-surface-heading"><div><span className="panel-icon"><Activity size={18} /></span><span><strong>{t("Audit history")}</strong><small>{t("Append-only evidence, ordered newest first and scoped by exact filters.")}</small></span></div><span className="audit-count">{t("{count} loaded", { count: rows.length })}</span></header>
      <form className="audit-filters" onSubmit={(event) => { event.preventDefault(); applyFilters(); }}>
        <label><span>{t("Action")}</span><input aria-label={t("Audit action")} placeholder="dataset.refresh_started" value={draft.action} onChange={(event) => setDraft({ ...draft, action: event.target.value })} /></label>
        <label><span>{t("Outcome")}</span><select aria-label={t("Audit outcome")} value={draft.outcome} onChange={(event) => setDraft({ ...draft, outcome: event.target.value as AuditOutcome | "" })}><option value="">{t("Any outcome")}</option><option value="success">{t("Success")}</option><option value="failure">{t("Failure")}</option><option value="throttled">{t("Throttled")}</option></select></label>
        <label><span>{t("Actor type")}</span><select aria-label={t("Audit actor type")} value={draft.actorType} onChange={(event) => setDraft({ ...draft, actorType: event.target.value as AuditActorType | "" })}><option value="">{t("Any actor")}</option><option value="user">{t("User")}</option><option value="service_account">{t("Service account")}</option><option value="anonymous">{t("Anonymous")}</option><option value="system">{t("System")}</option></select></label>
        <label><span>{t("Actor ID")}</span><input aria-label={t("Audit actor ID")} placeholder="UUID" value={draft.actorId} onChange={(event) => setDraft({ ...draft, actorId: event.target.value })} /></label>
        <label><span>{t("Resource type")}</span><input aria-label={t("Audit resource type")} placeholder="dataset" value={draft.resourceType} onChange={(event) => setDraft({ ...draft, resourceType: event.target.value })} /></label>
        <label><span>{t("Resource ID")}</span><input aria-label={t("Audit resource ID")} placeholder="UUID" value={draft.resourceId} onChange={(event) => setDraft({ ...draft, resourceId: event.target.value })} /></label>
        <label><span>{t("Request ID")}</span><input aria-label={t("Audit request ID")} placeholder={t("Trace one request")} value={draft.requestId} onChange={(event) => setDraft({ ...draft, requestId: event.target.value })} /></label>
        <label><span>{t("From")}</span><input aria-label={t("Audit from")} type="datetime-local" value={draft.occurredFrom} onChange={(event) => setDraft({ ...draft, occurredFrom: event.target.value })} /></label>
        <label><span>{t("To")}</span><input aria-label={t("Audit to")} type="datetime-local" value={draft.occurredTo} onChange={(event) => setDraft({ ...draft, occurredTo: event.target.value })} /></label>
        <div className="audit-filter-actions"><button className="button secondary small" type="button" onClick={clearFilters}>{t("Clear")}</button><button className="button primary small" type="submit"><Search size={15} /> {t("Apply filters")}</button></div>
        {filterError && <p className="filter-error">{filterError}</p>}
      </form>
      {events.isPending && <LoadingSurface label={t("Loading audit history")} />}
      {events.isError && <InlineError message={problemMessage(events.error)} onRetry={() => void events.refetch()} />}
      {!events.isPending && !events.isError && (
        <div className="audit-list" aria-label={t("Audit events")}>
          {rows.map((event) => <AuditRow key={event.id} event={event} open={expanded === event.id} onToggle={() => setExpanded(expanded === event.id ? null : event.id)} />)}
          {rows.length === 0 && <div className="empty-state compact"><Activity /><strong>{t("No audit events")}</strong><p>{t("Change the filters or wait for governed activity.")}</p></div>}
        </div>
      )}
      {events.hasNextPage && <button className="button secondary audit-more" type="button" onClick={() => void events.fetchNextPage()} disabled={events.isFetchingNextPage}>{events.isFetchingNextPage ? <LoaderCircle className="spin" size={16} /> : <ChevronDown size={16} />} {t("Load older events")}</button>}
    </section>
  );
}

function AuditRow({ event, open, onToggle }: { event: AuditEvent; open: boolean; onToggle: () => void }) {
  const { t } = useI18n();
  return <article className={`audit-row ${open ? "open" : ""}`}>
    <button type="button" onClick={onToggle} aria-expanded={open}>
      <span className={`audit-outcome ${event.outcome}`}>{event.outcome}</span>
      <span className="audit-action"><strong>{event.action}</strong><small>{event.actor_type.replace("_", " ")}{event.actor_id ? ` · ${event.actor_id}` : ""}</small></span>
      <span className="audit-resource"><small>{event.resource_type ?? t("No resource")}</small><strong>{event.resource_id ?? "—"}</strong></span>
      <time dateTime={event.occurred_at}>{formatDateTime(event.occurred_at)}</time>
      {open ? <ChevronUp size={17} /> : <ChevronDown size={17} />}
    </button>
    {open && <div className="audit-details"><dl><div><dt>{t("Event ID")}</dt><dd>{event.id}</dd></div><div><dt>{t("Request ID")}</dt><dd>{event.request_id ?? "—"}</dd></div></dl><strong>{t("Details")}</strong><pre>{JSON.stringify(event.details, null, 2)}</pre></div>}
  </article>;
}

function LoadingSurface({ label }: { label: string }) {
  return <div className="governance-loading"><LoaderCircle className="spin" size={18} /> {label}…</div>;
}

function field(key: PolicyField, label: string, hint: string, min: number, max: number, step?: number | "any", exclusiveMin = false): FieldSpec {
  return { key, label, hint, min, max, step, exclusiveMin };
}

function toDraft(policy: QuerySafetyPolicy): PolicyDraft {
  return Object.fromEntries(POLICY_FIELDS.map(({ key }) => [key, String(policy[key])])) as PolicyDraft;
}

function toPolicyInput(draft: PolicyDraft): QuerySafetyPolicyInput {
  return Object.fromEntries(POLICY_FIELDS.map(({ key }) => [key, Number(draft[key])])) as unknown as QuerySafetyPolicyInput;
}

function validatePolicy(draft: PolicyDraft): Partial<Record<PolicyField, string>> {
  const errors: Partial<Record<PolicyField, string>> = {};
  for (const spec of POLICY_FIELDS) {
    const value = Number(draft[spec.key]);
    if (draft[spec.key].trim() === "" || !Number.isFinite(value)) errors[spec.key] = "Enter a number";
    else if ((spec.exclusiveMin ? value <= spec.min : value < spec.min) || value > spec.max) errors[spec.key] = spec.exclusiveMin ? `Use a value above ${spec.min} and up to ${spec.max}` : `Use ${spec.min}–${spec.max}`;
    else if ((spec.step ?? 1) === 1 && !Number.isInteger(value)) errors[spec.key] = "Use a whole number";
  }
  for (const [warning, block] of [
    ["estimated_rows_warning", "estimated_rows_block"],
    ["total_cost_warning", "total_cost_block"],
    ["join_count_warning", "join_count_block"],
  ] as Array<[PolicyField, PolicyField]>) {
    if (Number(draft[warning]) >= Number(draft[block])) errors[warning] = "Must be below the block threshold";
  }
  return errors;
}

interface AuditFilterDraft {
  action: string; actorId: string; actorType: AuditActorType | ""; occurredFrom: string; occurredTo: string;
  outcome: AuditOutcome | ""; requestId: string; resourceId: string; resourceType: string;
}

const EMPTY_FILTERS: AuditFilterDraft = { action: "", actorId: "", actorType: "", occurredFrom: "", occurredTo: "", outcome: "", requestId: "", resourceId: "", resourceType: "" };

function auditFilters(draft: AuditFilterDraft): AuditFilters {
  return {
    action: draft.action.trim() || undefined,
    actorId: draft.actorId.trim() || undefined,
    actorType: draft.actorType || undefined,
    occurredFrom: draft.occurredFrom ? new Date(draft.occurredFrom).toISOString() : undefined,
    occurredTo: draft.occurredTo ? new Date(draft.occurredTo).toISOString() : undefined,
    outcome: draft.outcome || undefined,
    requestId: draft.requestId.trim() || undefined,
    resourceId: draft.resourceId.trim() || undefined,
    resourceType: draft.resourceType.trim() || undefined,
  };
}
