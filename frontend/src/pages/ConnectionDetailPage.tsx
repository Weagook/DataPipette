import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Check,
  CheckCircle2,
  Columns3,
  Database,
  KeyRound,
  LoaderCircle,
  LockKeyhole,
  Network,
  RefreshCw,
  Save,
  ShieldCheck,
  Table2,
  Trash2,
  UserRound,
  UsersRound,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useOutletContext, useParams } from "react-router-dom";

import { InlineError } from "../components/Status";
import {
  createConnection,
  createConnectionGrant,
  getCatalogRelation,
  getConnection,
  listCatalogRelations,
  listCatalogSchemas,
  listConnectionGrants,
  listGroups,
  listUsers,
  problemMessage,
  revokeConnectionGrant,
  testConnection,
  updateConnection,
  type Connection,
  type ConnectionCreate,
  type ConnectionGrant,
  type ConnectionScopeType,
  type CurrentUser,
  type GrantPrincipalType,
  type PostgresSslMode,
} from "../lib/api";
import { formatDateTime } from "../lib/format";
import { useI18n } from "../lib/i18n";
import { queryKeys } from "../lib/query";

const sslModes: Array<{ value: PostgresSslMode; label: string; note: string }> = [
  { value: "verify-full", label: "Verify full", note: "Certificate and hostname" },
  { value: "verify-ca", label: "Verify CA", note: "Certificate authority" },
  { value: "require", label: "Require TLS", note: "Encrypted, no verification" },
  { value: "disable", label: "Disable", note: "Local development only" },
];

interface ConnectionFormState {
  name: string;
  host: string;
  port: string;
  databaseName: string;
  username: string;
  password: string;
  sslMode: PostgresSslMode;
  caCertificate: string;
  removeCa: boolean;
}

const emptyForm: ConnectionFormState = {
  name: "",
  host: "",
  port: "5432",
  databaseName: "",
  username: "",
  password: "",
  sslMode: "verify-full",
  caCertificate: "",
  removeCa: false,
};

export function ConnectionDetailPage() {
  const user = useOutletContext<CurrentUser>();
  const { connectionId } = useParams();
  if (user.role !== "admin") return <Navigate to="/overview" replace />;
  return connectionId ? <ExistingConnection connectionId={connectionId} /> : <NewConnection />;
}

function NewConnection() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [form, setForm] = useState(emptyForm);
  const create = useMutation({
    mutationFn: () => createConnection(createPayload(form)),
    onSuccess: async (connection) => {
      queryClient.setQueryData(queryKeys.connection(connection.id), connection);
      await queryClient.invalidateQueries({ queryKey: queryKeys.connections });
      await navigate(`/connections/${connection.id}`, { replace: true });
    },
  });
  return (
    <ConnectionWorkspace
      form={form}
      onFormChange={setForm}
      onSave={() => create.mutate()}
      savePending={create.isPending}
      saveError={create.error}
    />
  );
}

function ExistingConnection({ connectionId }: { connectionId: string }) {
  const queryClient = useQueryClient();
  const connection = useQuery({
    queryKey: queryKeys.connection(connectionId),
    queryFn: () => getConnection(connectionId),
  });
  const [form, setForm] = useState(emptyForm);
  const [loadedAt, setLoadedAt] = useState<string | null>(null);

  useEffect(() => {
    if (connection.data && connection.data.updated_at !== loadedAt) {
      setForm(fromConnection(connection.data));
      setLoadedAt(connection.data.updated_at);
    }
  }, [connection.data, loadedAt]);

  const update = useMutation({
    mutationFn: () => updateConnection(connectionId, updatePayload(form)),
    onSuccess: async (value) => {
      setLoadedAt(value.updated_at);
      setForm(fromConnection(value));
      queryClient.setQueryData(queryKeys.connection(connectionId), value);
      await queryClient.invalidateQueries({ queryKey: queryKeys.connections });
    },
  });

  if (connection.isPending) return <div className="studio-panel skeleton tall" />;
  if (connection.isError) return <InlineError message={problemMessage(connection.error)} onRetry={() => void connection.refetch()} />;

  return (
    <ConnectionWorkspace
      connection={connection.data}
      form={form}
      onFormChange={setForm}
      onSave={() => update.mutate()}
      savePending={update.isPending}
      saveError={update.error}
    />
  );
}

function ConnectionWorkspace({ connection, form, onFormChange, onSave, savePending, saveError }: {
  connection?: Connection;
  form: ConnectionFormState;
  onFormChange: (value: ConnectionFormState) => void;
  onSave: () => void;
  savePending: boolean;
  saveError: unknown;
}) {
  const { t } = useI18n();
  const test = useMutation({ mutationFn: () => testConnection(connection!.id) });
  const requiresCa = form.sslMode === "verify-ca" || form.sslMode === "verify-full";
  const valid = Boolean(
    form.name.trim() && form.host.trim() && Number(form.port) >= 1 &&
    form.databaseName.trim() && form.username.trim() &&
    (connection || form.password) &&
    (!requiresCa || form.caCertificate.trim() || (connection?.has_ca_certificate && !form.removeCa)),
  );

  function submit(event: FormEvent) {
    event.preventDefault();
    if (valid) onSave();
  }

  return (
    <div className="page-stack connection-workspace">
      <Link className="back-link" to="/connections"><ArrowLeft size={16} /> {t("Connections")}</Link>
      <header className="page-heading connection-titlebar">
        <div>
          <span className="eyebrow">{t(connection ? "Source configuration" : "New PostgreSQL source")}</span>
          <h1>{connection ? connection.name : t("Add a connection")}</h1>
          <p>{t(connection ? "Manage safe connectivity, inspect the live catalog, and control analyst visibility." : "Credentials are encrypted at rest and are never returned by the API.")}</p>
        </div>
        {connection && <ConnectionStatus connection={connection} result={test.data} />}
      </header>

      <div className="connection-admin-grid">
        <form className="connection-config-card" onSubmit={submit}>
          <header>
            <span className="panel-icon"><Database size={18} /></span>
            <div><strong>{t("Connection settings")}</strong><small>{t("Only administrators can view this metadata or replace secrets.")}</small></div>
          </header>
          <div className="connection-form-grid">
            <Field label={t("Connection name")}><input required value={form.name} onChange={(e) => onFormChange({ ...form, name: e.target.value })} placeholder={t("Warehouse production")} /></Field>
            <Field label={t("Host")}><input required value={form.host} onChange={(e) => onFormChange({ ...form, host: e.target.value })} placeholder="db.internal.example" /></Field>
            <Field label={t("Port")}><input required type="number" min="1" max="65535" value={form.port} onChange={(e) => onFormChange({ ...form, port: e.target.value })} /></Field>
            <Field label={t("Database")}><input required value={form.databaseName} onChange={(e) => onFormChange({ ...form, databaseName: e.target.value })} placeholder="analytics" /></Field>
            <Field label={t("PostgreSQL role")}><input required autoComplete="username" value={form.username} onChange={(e) => onFormChange({ ...form, username: e.target.value })} placeholder="datapipette_reader" /></Field>
            <Field label={t(connection ? "Replace password" : "Password")} hint={t(connection ? "Leave empty to keep the encrypted password." : "Sent once and never returned.")}>
              <input required={!connection} type="password" autoComplete="new-password" value={form.password} onChange={(e) => onFormChange({ ...form, password: e.target.value })} placeholder={t(connection ? "Keep existing password" : "Source password")} />
            </Field>
          </div>

          <fieldset className="ssl-fieldset">
            <legend>{t("TLS policy")}</legend>
            <div className="ssl-options">
              {sslModes.map((mode) => (
                <label className={form.sslMode === mode.value ? "selected" : ""} key={mode.value}>
                  <input type="radio" name="ssl-mode" value={mode.value} checked={form.sslMode === mode.value} onChange={() => onFormChange({ ...form, sslMode: mode.value, removeCa: false })} />
                  <span><strong>{t(mode.label)}</strong><small>{t(mode.note)}</small></span>
                  {form.sslMode === mode.value && <Check size={15} />}
                </label>
              ))}
            </div>
          </fieldset>

          <Field label={t("CA certificate")} hint={t(requiresCa ? "PEM certificate required for verified TLS." : "Optional for this TLS policy.")}>
            <textarea value={form.caCertificate} onChange={(e) => onFormChange({ ...form, caCertificate: e.target.value, removeCa: false })} placeholder={connection?.has_ca_certificate ? t("A certificate is stored. Paste here to replace it.") : "-----BEGIN CERTIFICATE-----"} rows={5} />
          </Field>
          {connection?.has_ca_certificate && (
            <label className="remove-secret-check"><input type="checkbox" checked={form.removeCa} onChange={(e) => onFormChange({ ...form, removeCa: e.target.checked, caCertificate: "" })} /> {t("Remove the stored CA certificate on save")}</label>
          )}

          {(saveError || test.isError) && <InlineError message={problemMessage(saveError ?? test.error)} />}
          {test.data && (
            <div className={`test-result ${test.data.succeeded ? "success" : "failure"}`}>
              {test.data.succeeded ? <CheckCircle2 size={18} /> : <XCircle size={18} />}
              <div><strong>{t(test.data.succeeded ? "Connection succeeded" : "Connection failed safely")}</strong><span>{test.data.succeeded ? postgresVersion(test.data.server_version) : `${t("Code")}: ${test.data.error_code ?? "source_unavailable"}`} · {formatDateTime(test.data.tested_at)}</span></div>
            </div>
          )}

          <footer>
            {connection && <button className="button secondary" type="button" onClick={() => test.mutate()} disabled={test.isPending}><RefreshCw className={test.isPending ? "spin" : ""} size={16} />{t(test.isPending ? "Testing…" : "Test saved settings")}</button>}
            <button className="button primary" type="submit" disabled={!valid || savePending}><Save size={16} />{t(savePending ? "Saving…" : connection ? "Save settings" : "Create connection")}</button>
          </footer>
        </form>

        <aside className="connection-security-card">
          <LockKeyhole size={23} />
          <h2>{t("Secret-safe by default")}</h2>
          <p>{t("DataPipette stores encrypted credentials, sends no source secrets to Redis, and returns only safe connection-test codes.")}</p>
          <ul><li>{t("Use a dedicated read-only PostgreSQL role.")}</li><li>{t("Prefer Verify full outside local development.")}</li><li>{t("Grant only the catalog scope an analyst needs.")}</li></ul>
        </aside>
      </div>

      {connection && <CatalogAndAccess connection={connection} />}
    </div>
  );
}

function CatalogAndAccess({ connection }: { connection: Connection }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [schema, setSchema] = useState("");
  const [relation, setRelation] = useState("");
  const schemas = useQuery({ queryKey: queryKeys.catalogSchemas(connection.id), queryFn: () => listCatalogSchemas(connection.id) });
  const relations = useQuery({ queryKey: queryKeys.catalogRelations(connection.id, schema), queryFn: () => listCatalogRelations(connection.id, schema), enabled: Boolean(schema) });
  const detail = useQuery({ queryKey: queryKeys.catalogRelation(connection.id, schema, relation), queryFn: () => getCatalogRelation(connection.id, schema, relation), enabled: Boolean(schema && relation) });
  const grants = useQuery({ queryKey: queryKeys.connectionGrants(connection.id), queryFn: () => listConnectionGrants(connection.id) });
  const users = useQuery({ queryKey: queryKeys.users, queryFn: listUsers });
  const groups = useQuery({ queryKey: queryKeys.groups, queryFn: listGroups });
  const [principalType, setPrincipalType] = useState<GrantPrincipalType>("user");
  const [principalId, setPrincipalId] = useState("");
  const [scopeType, setScopeType] = useState<ConnectionScopeType>("all");

  useEffect(() => {
    if (!schema && schemas.data?.data[0]) setSchema(schemas.data.data[0].name);
  }, [schema, schemas.data]);
  const eligibleUsers = useMemo(() => (users.data ?? []).filter((value) => value.role === "analyst" && value.status === "active"), [users.data]);
  const principals = principalType === "user" ? eligibleUsers : groups.data ?? [];
  useEffect(() => {
    if (!principals.some((value) => value.id === principalId)) setPrincipalId(principals[0]?.id ?? "");
  }, [principalId, principals]);

  const createGrant = useMutation({
    mutationFn: () => createConnectionGrant(connection.id, {
      principal_type: principalType,
      principal_id: principalId,
      scope_type: scopeType,
      schema_name: scopeType === "all" ? null : schema,
      relation_name: scopeType === "table" ? relation : null,
    }),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: queryKeys.connectionGrants(connection.id) }),
  });
  const revoke = useMutation({
    mutationFn: (grantId: string) => revokeConnectionGrant(connection.id, grantId),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: queryKeys.connectionGrants(connection.id) }),
  });
  const grantReady = Boolean(principalId && (scopeType === "all" || schema) && (scopeType !== "table" || relation));

  return (
    <section className="catalog-access-section">
      <header className="section-heading"><div><span className="eyebrow">{t("Live source boundary")}</span><h2>{t("Catalog & access")}</h2><p>{t("Browse what the configured PostgreSQL role can see, then narrow it for DataPipette analysts.")}</p></div></header>
      {(schemas.isError || relations.isError || detail.isError) && <InlineError message={problemMessage(schemas.error ?? relations.error ?? detail.error)} onRetry={() => void schemas.refetch()} />}
      <div className="catalog-access-grid">
        <div className="catalog-browser-card">
          <header><span className="panel-icon"><Network size={18} /></span><div><strong>{t("Live catalog")}</strong><small>{t("Read directly from PostgreSQL with bounded read-only queries.")}</small></div></header>
          <div className="catalog-browser-body">
            <nav aria-label={t("Source schemas")}>
              <strong>{t("Schemas")}</strong>
              {schemas.isPending && <LoaderCircle className="spin" size={16} />}
              {schemas.data?.data.map((item) => <button className={schema === item.name ? "active" : ""} type="button" key={item.name} onClick={() => { setRelation(""); setSchema(item.name); }}><Database size={14} />{item.name}</button>)}
            </nav>
            <nav aria-label={t("Source relations")}>
              <strong>{t("Tables & views")}</strong>
              {relations.isFetching && <LoaderCircle className="spin" size={16} />}
              {relations.data?.data.map((item) => <button className={relation === item.name ? "active" : ""} type="button" key={`${item.schema_name}.${item.name}`} onClick={() => setRelation(item.name)}><Table2 size={14} /><span>{item.name}<small>{item.kind}</small></span></button>)}
            </nav>
            <div className="relation-inspector">
              {!relation && <div className="catalog-placeholder"><Columns3 size={24} /><strong>{t("Select a table")}</strong><p>{t("Inspect columns, keys, and safe join paths.")}</p></div>}
              {detail.isFetching && <LoaderCircle className="spin" />}
              {detail.data && <RelationDetail detail={detail.data} />}
            </div>
          </div>
        </div>

        <div className="connection-grants-card">
          <header><span className="panel-icon"><ShieldCheck size={18} /></span><div><strong>{t("Analyst visibility")}</strong><small>{t("Scopes add together across direct and group grants.")}</small></div></header>
          <form onSubmit={(event) => { event.preventDefault(); if (grantReady) createGrant.mutate(); }}>
            <div className="principal-tabs">
              <button className={principalType === "user" ? "active" : ""} type="button" onClick={() => setPrincipalType("user")}><UserRound size={14} /> {t("Analyst")}</button>
              <button className={principalType === "group" ? "active" : ""} type="button" onClick={() => setPrincipalType("group")}><UsersRound size={14} /> {t("Group")}</button>
            </div>
            <Field label={t(principalType === "user" ? "Active analyst" : "Group")}><select value={principalId} onChange={(e) => setPrincipalId(e.target.value)}><option value="">{t("Select a principal")}</option>{principals.map((item) => <option key={item.id} value={item.id}>{"display_name" in item ? `${item.display_name} · ${item.email}` : item.name}</option>)}</select></Field>
            <Field label={t("Visible scope")}><select value={scopeType} onChange={(e) => setScopeType(e.target.value as ConnectionScopeType)}><option value="all">{t("Everything visible to the DB role")}</option><option value="schema">{t("Selected schema")}</option><option value="table">{t("Selected table")}</option></select></Field>
            {scopeType !== "all" && <div className="scope-preview"><Database size={14} /><span>{schema || t("Select a schema in the catalog")}{scopeType === "table" && ` / ${relation || t("select a table")}`}</span></div>}
            {createGrant.isError && <InlineError message={problemMessage(createGrant.error)} />}
            <button className="button primary wide" type="submit" disabled={!grantReady || createGrant.isPending}>{createGrant.isPending ? <LoaderCircle className="spin" size={15} /> : <ShieldCheck size={15} />} {t("Grant source access")}</button>
          </form>
          <div className="grant-list">
            <strong>{t("Current grants")}</strong>
            {grants.isPending && <LoaderCircle className="spin" size={16} />}
            {grants.data?.map((grant) => <ConnectionGrantRow grant={grant} key={grant.id} users={users.data ?? []} groups={groups.data ?? []} onRevoke={() => revoke.mutate(grant.id)} revoking={revoke.isPending && revoke.variables === grant.id} />)}
            {!grants.isPending && grants.data?.length === 0 && <p>{t("No analyst can discover this source yet.")}</p>}
          </div>
        </div>
      </div>
    </section>
  );
}

function RelationDetail({ detail }: { detail: Awaited<ReturnType<typeof getCatalogRelation>> }) {
  const { t } = useI18n();
  return <div className="relation-detail-view">
    <header><span><strong>{detail.name}</strong><small>{detail.schema_name} · {detail.kind}</small></span><span>{t("{count} columns", { count: detail.columns.length })}</span></header>
    <div className="column-list">{detail.columns.map((column) => <div key={column.name}><span>{detail.primary_key.includes(column.name) && <KeyRound size={12} />}{column.name}</span><small>{column.data_type}{column.nullable ? ` · ${t("nullable")}` : ""}</small></div>)}</div>
    {detail.foreign_keys.length > 0 && <div className="foreign-key-list"><strong>{t("Visible relationships")}</strong>{detail.foreign_keys.map((key) => <p key={key.name}><Network size={12} /> {key.source_columns.join(", ")} → {key.target_schema}.{key.target_relation}.{key.target_columns.join(", ")}</p>)}</div>}
  </div>;
}

function ConnectionGrantRow({ grant, users, groups, onRevoke, revoking }: { grant: ConnectionGrant; users: Awaited<ReturnType<typeof listUsers>>; groups: Awaited<ReturnType<typeof listGroups>>; onRevoke: () => void; revoking: boolean }) {
  const { t } = useI18n();
  const user = users.find((value) => value.id === grant.principal_id);
  const group = groups.find((value) => value.id === grant.principal_id);
  const name = grant.principal_type === "user" ? user?.display_name ?? grant.principal_id : group?.name ?? grant.principal_id;
  const scope = grant.scope_type === "all" ? t("All visible objects") : grant.scope_type === "schema" ? grant.schema_name : `${grant.schema_name}.${grant.relation_name}`;
  return <article className="connection-grant-row"><span>{grant.principal_type === "user" ? <UserRound size={14} /> : <UsersRound size={14} />}</span><div><strong>{name}</strong><small>{scope}</small></div><button type="button" className="icon-button" onClick={onRevoke} disabled={revoking} aria-label={t("Revoke {name}", { name })}>{revoking ? <LoaderCircle className="spin" size={14} /> : <Trash2 size={14} />}</button></article>;
}

function ConnectionStatus({ connection, result }: { connection: Connection; result?: Awaited<ReturnType<typeof testConnection>> }) {
  const { t } = useI18n();
  const succeeded = result?.succeeded ?? connection.last_test_succeeded;
  const testedAt = result?.tested_at ?? connection.last_tested_at;
  return <span title={testedAt ? t("Tested {date}", { date: formatDateTime(testedAt) }) : undefined} className={`connection-title-status ${succeeded === true ? "healthy" : succeeded === false ? "failed" : "untested"}`}>{succeeded === true ? <CheckCircle2 size={16} /> : succeeded === false ? <XCircle size={16} /> : <RefreshCw size={16} />}{t(succeeded === true ? "Last test passed" : succeeded === false ? "Last test failed" : "Not tested")}</span>;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return <label className="connection-field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}

function createPayload(form: ConnectionFormState): ConnectionCreate {
  return { name: form.name.trim(), host: form.host.trim(), port: Number(form.port), database_name: form.databaseName.trim(), username: form.username.trim(), password: form.password, ssl_mode: form.sslMode, ca_certificate: form.caCertificate.trim() || null };
}

function updatePayload(form: ConnectionFormState) {
  return { name: form.name.trim(), host: form.host.trim(), port: Number(form.port), database_name: form.databaseName.trim(), username: form.username.trim(), ...(form.password ? { password: form.password } : {}), ssl_mode: form.sslMode, ...(form.caCertificate.trim() ? { ca_certificate: form.caCertificate.trim() } : form.removeCa ? { ca_certificate: null } : {}) };
}

function fromConnection(value: Connection): ConnectionFormState {
  return { name: value.name, host: value.host, port: String(value.port), databaseName: value.database_name, username: value.username, password: "", sslMode: value.ssl_mode, caCertificate: "", removeCa: false };
}

function postgresVersion(version: number | null): string {
  if (!version) return "PostgreSQL connected";
  const major = Math.floor(version / 10000);
  const minor = major >= 10 ? version % 10000 : Math.floor((version % 10000) / 100);
  return `PostgreSQL ${major}.${minor}`;
}
