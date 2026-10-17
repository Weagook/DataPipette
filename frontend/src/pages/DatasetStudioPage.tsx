import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  Braces,
  Check,
  ChevronRight,
  Clock3,
  Code2,
  Database,
  Eye,
  FileCheck2,
  Link2,
  LoaderCircle,
  Play,
  Plus,
  RefreshCw,
  Save,
  ShieldCheck,
  Table2,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Link,
  Navigate,
  useNavigate,
  useOutletContext,
  useParams,
} from "react-router-dom";

import { InlineError } from "../components/Status";
import {
  analyzeDatasetImpact,
  createDataset,
  DatePipetteApiError,
  getCatalogRelation,
  getDataset,
  getDatasetDraft,
  getRefreshRun,
  getRefreshSchedule,
  listCatalogRelations,
  listCatalogSchemas,
  listConnections,
  previewDatasetDraft,
  problemMessage,
  renameDataset,
  replaceDatasetDraft,
  replaceRefreshSchedule,
  requestDatasetRefresh,
  suggestCatalogJoins,
  validateDatasetDraft,
  type ApiSchemas,
  type CatalogRelation,
  type CurrentUser,
  type DatasetDefinition,
  type DatasetImpact,
  type DatasetPreview,
  type DatasetValidation,
  type RefreshRun,
  type RefreshScheduleInput,
} from "../lib/api";
import { formatDateTime } from "../lib/format";
import { randomUuid } from "../lib/id";
import { queryKeys } from "../lib/query";
import {
  appendVisualField,
  appendJoinedRelation,
  definitionIsReady,
  emptySqlDefinition,
  newSqlField,
  visualDefinitionFromRelation,
  type DisplayFormat,
  type SqlDefinition,
  type VisualDefinition,
} from "../lib/studioDefinition";

type StudioTab = "build" | "check" | "preview" | "publish";
type RelationChoice = { schema: string; name: string };
const FORMATS: { value: "" | DisplayFormat; label: string }[] = [
  { value: "", label: "Automatic" },
  { value: "date_dd_mm_yyyy", label: "DD-MM-YYYY" },
  { value: "date_yyyy_mm_dd", label: "YYYY-MM-DD" },
  { value: "datetime_dd_mm_yyyy_hh_mm", label: "DD-MM-YYYY HH:mm" },
  { value: "number_integer", label: "Whole number" },
  { value: "number_decimal_2", label: "2 decimal places" },
  { value: "percent", label: "Percent" },
];

export function DatasetStudioPage() {
  const user = useOutletContext<CurrentUser>();
  const { datasetId } = useParams();
  if (user.role === "viewer") return <Navigate to="/overview" replace />;
  return datasetId ? (
    <ExistingStudio datasetId={datasetId} user={user} />
  ) : (
    <NewStudio />
  );
}

function NewStudio() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [connectionId, setConnectionId] = useState("");
  const [mode, setMode] = useState<"visual" | "sql">("visual");
  const [definition, setDefinition] = useState<DatasetDefinition | null>(null);
  const connections = useQuery({
    queryKey: queryKeys.connections,
    queryFn: () => listConnections(),
  });
  const create = useMutation({
    mutationFn: () => createDataset(name.trim(), connectionId, definition!),
    onSuccess: async (dataset) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.datasets });
      navigate(`/datasets/${dataset.id}/draft`, { replace: true });
    },
  });

  useEffect(() => {
    if (!connectionId && connections.data?.data[0])
      setConnectionId(connections.data.data[0].id);
  }, [connectionId, connections.data]);

  function changeMode(next: "visual" | "sql") {
    setMode(next);
    setDefinition(next === "sql" ? emptySqlDefinition() : null);
  }

  const ready = definitionIsReady(definition);
  const guidance = !name.trim()
    ? "Give the dataset a clear name"
    : !connectionId
      ? "Choose a PostgreSQL connection"
      : mode === "visual" && !definition
        ? "Choose the first table to open the builder"
        : definition?.kind === "visual" && definition.fields.length === 0
          ? "Select at least one output column"
          : definition?.kind === "sql" && !definition.statement.trim()
            ? "Write a read-only SELECT or WITH query"
            : definition?.kind === "sql" && definition.fields.length === 0
              ? "Declare at least one SQL result field"
              : !ready
                ? "Use unique non-empty relation aliases and output names"
                : "Ready to create a private editable draft";

  return (
    <div className="page-stack studio-page">
      <StudioHeading
        title="New dataset"
        subtitle="Private by default · visible only to you and administrators"
      />
      {connections.isError && (
        <InlineError
          message={problemMessage(connections.error)}
          onRetry={() => void connections.refetch()}
        />
      )}
      <ol className="dataset-create-steps" aria-label="Dataset creation steps">
        <li className={name.trim() && connectionId ? "complete" : "active"}>
          <span>1</span><div><strong>Setup</strong><small>Name and connection</small></div>
        </li>
        <li className={definition ? "complete" : name.trim() && connectionId ? "active" : ""}>
          <span>2</span><div><strong>Build</strong><small>Tables or SQL</small></div>
        </li>
        <li className={ready && name.trim() ? "active" : ""}>
          <span>3</span><div><strong>Create draft</strong><small>Then validate and preview</small></div>
        </li>
      </ol>
      <section className="studio-create-card dataset-setup-card">
        <div className="studio-create-intro">
          <span className="panel-icon">
            <Database size={20} />
          </span>
          <div>
            <strong>1. Set up the dataset</strong>
            <p>
              Choose where the data comes from. Nothing reads business rows yet.
            </p>
          </div>
        </div>
        <div className="studio-form-grid">
          <label>
            <span>Dataset name</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Revenue by customer"
              autoFocus
            />
          </label>
          <label>
            <span>PostgreSQL connection</span>
            <select
              value={connectionId}
              onChange={(event) => {
                setConnectionId(event.target.value);
                setDefinition(mode === "sql" ? emptySqlDefinition() : null);
              }}
              disabled={connections.isPending}
            >
              <option value="">Select a connection</option>
              {connections.data?.data.map((connection) => (
                <option key={connection.id} value={connection.id}>
                  {connection.name} · {connection.database_name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="mode-cards" role="radiogroup" aria-label="Dataset mode">
          <button
            className={mode === "visual" ? "selected" : ""}
            onClick={() => changeMode("visual")}
            type="button"
            role="radio"
            aria-checked={mode === "visual"}
          >
            <Table2 />
            <span>
              <strong>Visual builder</strong>
              <small>Choose tables and follow safe foreign-key joins.</small>
            </span>
            <Check />
          </button>
          <button
            className={mode === "sql" ? "selected" : ""}
            onClick={() => changeMode("sql")}
            type="button"
            role="radio"
            aria-checked={mode === "sql"}
          >
            <Code2 />
            <span>
              <strong>Advanced SQL</strong>
              <small>
                Write one read-only PostgreSQL SELECT or WITH query.
              </small>
            </span>
            <Check />
          </button>
        </div>
      </section>
      <div className="new-dataset-workspace">
        <div className="new-dataset-builder">
          {!connectionId && (
            <section className="studio-panel builder-empty-state">
              <Database />
              <strong>Choose a connection to start building</strong>
              <p>The source catalog and SQL editor stay scoped to that PostgreSQL connection.</p>
            </section>
          )}
          {connectionId && mode === "visual" && !definition && (
            <CatalogStarter
              connectionId={connectionId}
              onChoose={(detail) =>
                setDefinition(visualDefinitionFromRelation(detail))
              }
            />
          )}
          {connectionId && definition?.kind === "visual" && (
            <section className="studio-panel new-builder-panel">
              <header>
                <div>
                  <span className="panel-icon"><Table2 /></span>
                  <span>
                    <strong>2. Build the visual model</strong>
                    <small>Select output columns, labels, formats, and related tables.</small>
                  </span>
                </div>
                <button className="button secondary small" onClick={() => setDefinition(null)} type="button">
                  Change first table
                </button>
              </header>
              <VisualEditor
                connectionId={connectionId}
                definition={definition}
                onChange={setDefinition}
              />
            </section>
          )}
          {connectionId && definition?.kind === "sql" && (
            <section className="studio-panel new-builder-panel sql-builder-panel">
              <header>
                <div>
                  <span className="panel-icon"><Code2 /></span>
                  <span>
                    <strong>2. Write the SQL definition</strong>
                    <small>The original query stays editable in the saved draft.</small>
                  </span>
                </div>
              </header>
              <SqlEditor definition={definition} onChange={setDefinition} autoFocus />
            </section>
          )}
        </div>
        <aside className="new-dataset-side">
          <CreationReadiness
            name={name}
            connectionId={connectionId}
            mode={mode}
            definition={definition}
          />
          {definition && <JsonInspector definition={definition} />}
        </aside>
      </div>
      {create.isError && <InlineError message={problemMessage(create.error)} />}
      <div className="studio-sticky-actions">
        <span>{guidance}</span>
        <button
          className="button primary"
          disabled={
            !name.trim() ||
            !connectionId ||
            !ready ||
            create.isPending
          }
          onClick={() => create.mutate()}
          type="button"
        >
          {create.isPending ? <LoaderCircle className="spin" /> : <Plus />}{" "}
          Create dataset
        </button>
      </div>
    </div>
  );
}

function ExistingStudio({
  datasetId,
  user,
}: {
  datasetId: string;
  user: CurrentUser;
}) {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<StudioTab>("build");
  const [name, setName] = useState("");
  const [definition, setDefinition] = useState<DatasetDefinition | null>(null);
  const [etag, setEtag] = useState("");
  const [validation, setValidation] = useState<DatasetValidation | null>(null);
  const [preview, setPreview] = useState<DatasetPreview | null>(null);
  const [impact, setImpact] = useState<DatasetImpact | null>(null);
  const [run, setRun] = useState<RefreshRun | null>(null);
  const initialized = useRef(false);
  const publicationSyncedRunId = useRef<string | null>(null);
  const dataset = useQuery({
    queryKey: queryKeys.dataset(datasetId),
    queryFn: () => getDataset(datasetId),
  });
  const draft = useQuery({
    queryKey: queryKeys.datasetDraft(datasetId),
    queryFn: () => getDatasetDraft(datasetId),
  });
  const savedDefinition = draft.data?.draft.definition ?? null;
  const dirty =
    Boolean(
      definition &&
        savedDefinition &&
        JSON.stringify(definition) !== JSON.stringify(savedDefinition),
    ) || Boolean(dataset.data && name !== dataset.data.name);

  useEffect(() => {
    if (!initialized.current && dataset.data && draft.data) {
      initialized.current = true;
      setName(dataset.data.name);
      setDefinition(draft.data.draft.definition);
      setEtag(draft.data.etag);
      setValidation(draft.data.draft.validation);
    }
  }, [dataset.data, draft.data]);

  const save = useMutation({
    mutationFn: async () => {
      if (!definition || !dataset.data)
        throw new Error("The draft is not ready.");
      if (name.trim() !== dataset.data.name)
        await renameDataset(datasetId, name.trim());
      if (JSON.stringify(definition) !== JSON.stringify(savedDefinition))
        return replaceDatasetDraft(datasetId, definition, etag);
      return draft.data!;
    },
    onSuccess: async (value) => {
      setEtag(value.etag);
      setValidation(value.draft.validation);
      setPreview(null);
      setImpact(null);
      queryClient.setQueryData(queryKeys.datasetDraft(datasetId), value);
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.dataset(datasetId),
        }),
        queryClient.invalidateQueries({ queryKey: queryKeys.datasets }),
      ]);
    },
  });
  const validate = useMutation({
    mutationFn: () => validateDatasetDraft(datasetId, etag),
    onSuccess: (value) => {
      setValidation(value);
      setPreview(null);
      setImpact(null);
      setTab("check");
    },
  });
  const previewMutation = useMutation({
    mutationFn: () => previewDatasetDraft(datasetId, etag, 100),
    onSuccess: (value) => {
      setPreview(value);
      setTab("preview");
    },
  });
  const impactMutation = useMutation({
    mutationFn: () => analyzeDatasetImpact(datasetId, etag),
    onSuccess: (value) => {
      setImpact(value);
      setTab("publish");
    },
  });
  const publish = useMutation({
    mutationFn: () => requestDatasetRefresh(datasetId, etag),
    onSuccess: (value) => {
      setRun(value);
      setTab("publish");
    },
  });
  const runQuery = useQuery({
    queryKey: queryKeys.refreshRun(datasetId, run?.id ?? "none"),
    queryFn: () => getRefreshRun(datasetId, run!.id),
    enabled: Boolean(run),
    refetchInterval: (query) =>
      query.state.data &&
      ["succeeded", "failed", "cancelled"].includes(query.state.data.state)
        ? false
        : 1500,
  });
  useEffect(() => {
    if (runQuery.data) setRun(runQuery.data);
  }, [runQuery.data]);
  useEffect(() => {
    if (
      run?.state !== "succeeded" ||
      publicationSyncedRunId.current === run.id
    )
      return;

    publicationSyncedRunId.current = run.id;
    let active = true;
    void (async () => {
      try {
        const [nextDataset, nextDraft] = await Promise.all([
          getDataset(datasetId),
          getDatasetDraft(datasetId),
        ]);
        queryClient.setQueryData(queryKeys.dataset(datasetId), nextDataset);
        queryClient.setQueryData(queryKeys.datasetDraft(datasetId), nextDraft);
        void queryClient.invalidateQueries({ queryKey: queryKeys.datasets });
        if (!active) return;
        setName(nextDataset.name);
        setDefinition(nextDraft.draft.definition);
        setEtag(nextDraft.etag);
        setValidation(nextDraft.draft.validation);
        setPreview(null);
        setImpact(null);
      } catch {
        await Promise.all([
          queryClient.invalidateQueries({
            queryKey: queryKeys.dataset(datasetId),
          }),
          queryClient.invalidateQueries({
            queryKey: queryKeys.datasetDraft(datasetId),
          }),
          queryClient.invalidateQueries({ queryKey: queryKeys.datasets }),
        ]);
      }
    })();

    return () => {
      active = false;
    };
  }, [datasetId, queryClient, run?.id, run?.state]);

  if (dataset.isPending || draft.isPending || !definition)
    return <div className="dataset-table-skeleton skeleton" />;
  if (dataset.isError || draft.isError)
    return (
      <InlineError
        message={problemMessage(dataset.error ?? draft.error)}
        onRetry={() => {
          void dataset.refetch();
          void draft.refetch();
        }}
      />
    );
  const actionError =
    save.error ??
    validate.error ??
    previewMutation.error ??
    impactMutation.error ??
    publish.error;
  const valid =
    validation?.validated_revision === draft.data.draft.revision &&
    ["valid", "valid_with_warnings"].includes(validation.state);

  return (
    <div className="page-stack studio-page">
      <StudioHeading
        title={name || dataset.data.name}
        subtitle={`Draft revision ${draft.data.draft.revision} · ${definition.kind === "visual" ? "Visual model" : "Advanced SQL"}`}
      />
      <div className="studio-commandbar">
        <nav aria-label="Dataset workflow">
          {(["build", "check", "preview", "publish"] as StudioTab[]).map(
            (item, index) => (
              <button
                className={tab === item ? "active" : ""}
                onClick={() => setTab(item)}
                key={item}
                type="button"
              >
                <span>{index + 1}</span>
                {
                  {
                    build: "Build",
                    check: "Validate",
                    preview: "Preview",
                    publish: "Publish",
                  }[item]
                }
              </button>
            ),
          )}
        </nav>
        <div>
          <span className={dirty ? "unsaved" : "saved"}>
            {dirty ? "Unsaved changes" : "Saved"}
          </span>
          <button
            className="button primary small"
            disabled={
              !dirty ||
              save.isPending ||
              !name.trim() ||
              !definitionIsReady(definition)
            }
            onClick={() => save.mutate()}
            type="button"
          >
            {save.isPending ? <LoaderCircle className="spin" /> : <Save />} Save
            draft
          </button>
        </div>
      </div>
      {actionError && <InlineError message={problemMessage(actionError)} />}
      {tab === "build" && (
        <BuildWorkspace
          datasetId={datasetId}
          connectionId={dataset.data.connection_id}
          name={name}
          onNameChange={setName}
          definition={definition}
          onDefinitionChange={(value) => {
            setDefinition(value);
            setValidation(null);
            setPreview(null);
            setImpact(null);
          }}
        />
      )}
      {tab === "check" && (
        <ValidationWorkspace
          validation={validation}
          dirty={dirty}
          pending={validate.isPending}
          onValidate={() => validate.mutate()}
          isAdmin={user.role === "admin"}
        />
      )}
      {tab === "preview" && (
        <PreviewWorkspace
          preview={preview}
          disabled={dirty || !valid}
          pending={previewMutation.isPending}
          onPreview={() => previewMutation.mutate()}
        />
      )}
      {tab === "publish" && (
        <PublishWorkspace
          datasetId={datasetId}
          dataset={dataset.data}
          dirty={dirty}
          valid={valid}
          impact={impact}
          run={run}
          impactPending={impactMutation.isPending}
          publishPending={publish.isPending}
          onImpact={() => impactMutation.mutate()}
          onPublish={() => publish.mutate()}
        />
      )}
    </div>
  );
}

function StudioHeading({
  title,
  subtitle,
}: {
  title: string;
  subtitle: string;
}) {
  return (
    <header className="page-heading dataset-titlebar">
      <div>
        <Link className="back-link" to="/datasets">
          <ArrowLeft size={15} /> Datasets
        </Link>
        <span className="eyebrow">Dataset Studio</span>
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>
      <span className="trust-pill">
        <ShieldCheck size={16} /> Governed workflow
      </span>
    </header>
  );
}

function CreationReadiness({
  name,
  connectionId,
  mode,
  definition,
}: {
  name: string;
  connectionId: string;
  mode: "visual" | "sql";
  definition: DatasetDefinition | null;
}) {
  const aliases =
    definition?.kind === "visual"
      ? definition.source.relations.map((relation) => relation.alias.trim())
      : [];
  const sourceReady =
    mode === "visual"
      ? definition?.kind === "visual" &&
        definition.source.relations.length > 0 &&
        aliases.every(Boolean) &&
        new Set(aliases).size === aliases.length
      : definition?.kind === "sql" && definition.statement.trim().length > 0;
  const fieldNames = definition?.fields.map((field) => field.name.trim()) ?? [];
  const fieldsReady =
    fieldNames.length > 0 &&
    fieldNames.every(Boolean) &&
    new Set(fieldNames).size === fieldNames.length;
  return (
    <section className="creation-readiness" aria-label="Creation readiness">
      <header>
        <FileCheck2 />
        <div>
          <strong>Draft readiness</strong>
          <small>Complete these before creating the dataset.</small>
        </div>
      </header>
      <ul>
        <ReadinessItem ready={Boolean(name.trim())} label="Dataset named" />
        <ReadinessItem ready={Boolean(connectionId)} label="Connection selected" />
        <ReadinessItem
          ready={Boolean(sourceReady)}
          label={mode === "visual" ? "First table selected" : "SQL query written"}
        />
        <ReadinessItem ready={fieldsReady} label="Output fields valid" />
      </ul>
      <p>Creation saves a private JSON draft. It does not query or publish source rows.</p>
    </section>
  );
}

function ReadinessItem({ ready, label }: { ready: boolean; label: string }) {
  return (
    <li className={ready ? "ready" : ""}>
      <span><Check /></span>
      {label}
    </li>
  );
}

function BuildWorkspace({
  datasetId,
  connectionId,
  name,
  onNameChange,
  definition,
  onDefinitionChange,
}: {
  datasetId: string;
  connectionId: string;
  name: string;
  onNameChange: (value: string) => void;
  definition: DatasetDefinition;
  onDefinitionChange: (value: DatasetDefinition) => void;
}) {
  return (
    <div className="studio-layout">
      <main className="studio-main">
        <section className="studio-panel">
          <header>
            <div>
              <span className="panel-icon">
                {definition.kind === "visual" ? <Table2 /> : <Code2 />}
              </span>
              <span>
                <strong>
                  {definition.kind === "visual"
                    ? "Visual model"
                    : "Advanced SQL"}
                </strong>
                <small>Saved as a versioned, editable definition</small>
              </span>
            </div>
          </header>
          <label className="studio-name">
            <span>Dataset name</span>
            <input
              value={name}
              onChange={(event) => onNameChange(event.target.value)}
            />
          </label>
          {definition.kind === "visual" ? (
            <VisualEditor
              connectionId={connectionId}
              definition={definition}
              onChange={onDefinitionChange}
            />
          ) : (
            <SqlEditor definition={definition} onChange={onDefinitionChange} />
          )}
        </section>
      </main>
      <aside className="studio-side">
        <JsonInspector definition={definition} />
        <div className="studio-note">
          <Braces />
          <div>
            <strong>Stable draft contract</strong>
            <p>
              Relation, field, and join IDs stay stable while you edit. This is
              the JSON the API stores and reopens.
            </p>
          </div>
        </div>
        <Link
          className="button secondary wide"
          to={`/datasets/${datasetId}/table`}
        >
          <Eye size={16} /> Open published table
        </Link>
      </aside>
    </div>
  );
}

function CatalogStarter({
  connectionId,
  onChoose,
  chosen,
}: {
  connectionId: string;
  onChoose: (detail: CatalogRelation) => void;
  chosen?: { schema: string; name: string };
}) {
  return (
    <section className="studio-panel catalog-starter">
      <header>
        <div>
          <span className="panel-icon">
            <Database />
          </span>
          <span>
            <strong>Choose the first table</strong>
            <small>Live catalog metadata; no business rows are read.</small>
          </span>
        </div>
      </header>
      <CatalogPicker
        connectionId={connectionId}
        onChoose={onChoose}
        buttonLabel="Use table"
      />
      {chosen && (
        <p className="selection-confirm">
          <Check /> Selected {chosen.schema}.{chosen.name}
        </p>
      )}
    </section>
  );
}

function CatalogPicker({
  connectionId,
  onChoose,
  buttonLabel,
}: {
  connectionId: string;
  onChoose: (detail: CatalogRelation) => void;
  buttonLabel: string;
}) {
  const [schema, setSchema] = useState("");
  const [relation, setRelation] = useState("");
  const schemas = useQuery({
    queryKey: queryKeys.catalogSchemas(connectionId),
    queryFn: () => listCatalogSchemas(connectionId),
    enabled: Boolean(connectionId),
  });
  const relations = useQuery({
    queryKey: queryKeys.catalogRelations(connectionId, schema),
    queryFn: () => listCatalogRelations(connectionId, schema),
    enabled: Boolean(schema),
  });
  const detail = useQuery({
    queryKey: queryKeys.catalogRelation(connectionId, schema, relation),
    queryFn: () => getCatalogRelation(connectionId, schema, relation),
    enabled: Boolean(schema && relation),
  });
  useEffect(() => {
    if (!schema && schemas.data?.data[0]) setSchema(schemas.data.data[0].name);
  }, [schema, schemas.data]);
  useEffect(() => {
    setRelation("");
  }, [schema]);
  return (
    <div className="catalog-picker">
      <div className="catalog-selects">
        <label>
          <span>Schema</span>
          <select
            value={schema}
            onChange={(event) => setSchema(event.target.value)}
          >
            <option value="">Select schema</option>
            {schemas.data?.data.map((item) => (
              <option value={item.name} key={item.name}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <ChevronRight />
        <label>
          <span>Table or view</span>
          <select
            value={relation}
            onChange={(event) => setRelation(event.target.value)}
            disabled={!schema}
          >
            <option value="">Select relation</option>
            {relations.data?.data.map((item) => (
              <option
                value={item.name}
                key={`${item.schema_name}.${item.name}`}
              >
                {item.name} · {item.kind}
              </option>
            ))}
          </select>
        </label>
      </div>
      {detail.data && (
        <div className="catalog-detail">
          <div>
            <strong>
              {detail.data.schema_name}.{detail.data.name}
            </strong>
            <small>
              {detail.data.columns.length} columns · primary key{" "}
              {detail.data.primary_key.join(", ") || "not declared"}
            </small>
          </div>
          <div className="catalog-columns">
            {detail.data.columns.slice(0, 8).map((column) => (
              <span key={column.name}>
                <code>{column.name}</code>
                <small>{column.data_type}</small>
              </span>
            ))}
            {detail.data.columns.length > 8 && (
              <span>+{detail.data.columns.length - 8} more</span>
            )}
          </div>
          <button
            className="button primary small"
            onClick={() => onChoose(detail.data)}
            type="button"
          >
            {buttonLabel}
          </button>
        </div>
      )}
      {(schemas.error || relations.error || detail.error) && (
        <div className="catalog-picker-error">
          <InlineError
            message={problemMessage(
              schemas.error ?? relations.error ?? detail.error,
            )}
          />
          <p>
            Ask an administrator to retest this connection and confirm that
            PostgreSQL accepts traffic from the DatePipette server.
          </p>
        </div>
      )}
    </div>
  );
}

function VisualEditor({
  connectionId,
  definition,
  onChange,
}: {
  connectionId: string;
  definition: VisualDefinition;
  onChange: (value: DatasetDefinition) => void;
}) {
  const [adding, setAdding] = useState(false);
  const [joinError, setJoinError] = useState("");
  async function addJoined(detail: CatalogRelation) {
    setJoinError("");
    const candidateId = randomUuid();
    try {
      const suggestions = await suggestCatalogJoins(connectionId, [
        ...definition.source.relations.map((item) => ({
          id: item.id,
          schema: item.schema,
          name: item.name,
        })),
        { id: candidateId, schema: detail.schema_name, name: detail.name },
      ]);
      const suggestion = suggestions.find(
        (item) =>
          item.source_relation_id === candidateId ||
          item.target_relation_id === candidateId,
      );
      if (!suggestion) {
        setJoinError(
          "No visible foreign-key relationship connects this table to the current model.",
        );
        return;
      }
      onChange(
        appendJoinedRelation(definition, detail, suggestion, candidateId),
      );
      setAdding(false);
    } catch (error) {
      setJoinError(problemMessage(error));
    }
  }
  return (
    <div className="visual-editor">
      <div className="relation-strip">
        {definition.source.relations.map((relation, index) => (
          <div className="relation-chip" key={relation.id}>
            <span>{index === 0 ? <Database /> : <Link2 />}</span>
            <div>
              <input
                aria-label={`Alias for ${relation.schema}.${relation.name}`}
                value={relation.alias}
                onChange={(event) =>
                  onChange({
                    ...definition,
                    source: {
                      relations: definition.source.relations.map((item) =>
                        item.id === relation.id
                          ? { ...item, alias: event.target.value }
                          : item,
                      ),
                    },
                  })
                }
              />
              <small>
                {relation.schema}.{relation.name}
              </small>
            </div>
            {index > 0 &&
              !definition.joins?.some(
                (join) => join.left_relation_id === relation.id,
              ) && (
                <button
                  className="icon-button"
                  aria-label={`Remove table ${relation.schema}.${relation.name}`}
                  onClick={() =>
                    onChange({
                      ...definition,
                      source: {
                        relations: definition.source.relations.filter(
                          (item) => item.id !== relation.id,
                        ),
                      },
                      fields: definition.fields.filter(
                        (field) => field.source.relation_id !== relation.id,
                      ),
                      joins: definition.joins?.filter(
                        (join) =>
                          join.left_relation_id !== relation.id &&
                          join.right_relation_id !== relation.id,
                      ),
                    })
                  }
                  type="button"
                >
                  <Trash2 />
                </button>
              )}
          </div>
        ))}
        <button
          className="add-relation"
          onClick={() => setAdding((value) => !value)}
          type="button"
        >
          <Plus /> Add joined table
        </button>
      </div>
      {adding && (
        <div className="join-picker">
          <p>
            <Link2 /> Only tables connected by a visible foreign key can be
            added.
          </p>
          <CatalogPicker
            connectionId={connectionId}
            onChoose={(detail) => void addJoined(detail)}
            buttonLabel="Find relationship"
          />
          {joinError && <InlineError message={joinError} />}
        </div>
      )}
      {Boolean(definition.joins?.length) && (
        <JoinEditor definition={definition} onChange={onChange} />
      )}
      <section className="source-columns-section">
        <div className="field-header">
          <span>Source columns</span>
          <small>Choose which columns become dataset output fields</small>
        </div>
        <div className="source-column-groups">
          {definition.source.relations.map((relation) => (
            <SourceColumnSelector
              key={relation.id}
              connectionId={connectionId}
              relation={relation}
              definition={definition}
              onChange={onChange}
            />
          ))}
        </div>
      </section>
      <FieldEditor definition={definition} onChange={onChange} />
    </div>
  );
}

function JoinEditor({
  definition,
  onChange,
}: {
  definition: VisualDefinition;
  onChange: (value: DatasetDefinition) => void;
}) {
  const relationNames = new Map(
    definition.source.relations.map((relation) => [relation.id, relation.alias]),
  );
  return (
    <section className="join-editor">
      <div className="field-header">
        <span>Relationships</span>
        <small>Foreign-key suggestions become explicit editable joins</small>
      </div>
      {definition.joins?.map((join) => (
        <article key={join.id}>
          <div className="join-path">
            <Link2 />
            <strong>{relationNames.get(join.left_relation_id)}</strong>
            <span>to</span>
            <strong>{relationNames.get(join.right_relation_id)}</strong>
          </div>
          <label>
            <span>Join type</span>
            <select
              aria-label={`Join type for ${relationNames.get(join.right_relation_id)}`}
              value={join.type}
              onChange={(event) =>
                onChange({
                  ...definition,
                  joins: definition.joins?.map((item) =>
                    item.id === join.id
                      ? { ...item, type: event.target.value as "inner" | "left" }
                      : item,
                  ),
                })
              }
            >
              <option value="left">LEFT JOIN</option>
              <option value="inner">INNER JOIN</option>
            </select>
          </label>
          <div className="join-conditions">
            {join.conditions.map((condition) => (
              <code key={`${condition.left_column}:${condition.right_column}`}>
                {relationNames.get(join.left_relation_id)}.{condition.left_column}
                <span>=</span>
                {relationNames.get(join.right_relation_id)}.{condition.right_column}
              </code>
            ))}
          </div>
        </article>
      ))}
    </section>
  );
}

function SourceColumnSelector({
  connectionId,
  relation,
  definition,
  onChange,
}: {
  connectionId: string;
  relation: VisualDefinition["source"]["relations"][number];
  definition: VisualDefinition;
  onChange: (value: DatasetDefinition) => void;
}) {
  const detail = useQuery({
    queryKey: queryKeys.catalogRelation(
      connectionId,
      relation.schema,
      relation.name,
    ),
    queryFn: () =>
      getCatalogRelation(connectionId, relation.schema, relation.name),
  });
  const selected = new Set(
    definition.fields
      .filter((field) => field.source.relation_id === relation.id)
      .map((field) => field.source.column),
  );
  return (
    <article className="source-column-group">
      <header>
        <div>
          <strong>{relation.alias}</strong>
          <small>{relation.schema}.{relation.name}</small>
        </div>
        <span>{selected.size} selected</span>
      </header>
      {detail.isPending && <div className="column-picker-skeleton skeleton" />}
      {detail.isError && <InlineError message={problemMessage(detail.error)} />}
      <div className="source-column-list">
        {detail.data?.columns.map((column) => {
          const checked = selected.has(column.name);
          return (
            <label key={column.name}>
              <input
                type="checkbox"
                checked={checked}
                onChange={(event) =>
                  onChange(
                    event.target.checked
                      ? appendVisualField(definition, relation.id, column.name)
                      : {
                          ...definition,
                          fields: definition.fields.filter(
                            (field) =>
                              !(
                                field.source.relation_id === relation.id &&
                                field.source.column === column.name
                              ),
                          ),
                        },
                  )
                }
              />
              <span>
                <strong>{column.name}</strong>
                <small>{column.data_type}{column.nullable ? " · nullable" : ""}</small>
              </span>
            </label>
          );
        })}
      </div>
    </article>
  );
}

function FieldEditor({
  definition,
  onChange,
}: {
  definition: VisualDefinition;
  onChange: (value: DatasetDefinition) => void;
}) {
  const relations = new Map(
    definition.source.relations.map((relation) => [
      relation.id,
      relation.alias,
    ]),
  );
  return (
    <div className="field-editor">
      <div className="field-header">
        <span>Output fields</span>
        <small>{definition.fields.length} selected</small>
      </div>
      {definition.fields.map((field) => (
        <div className="field-row" key={field.id}>
          <div className="field-source">
            <strong>{field.name}</strong>
            <small>
              {relations.get(field.source.relation_id)}.{field.source.column}
            </small>
          </div>
          <label>
            <span>Output name</span>
            <input
              value={field.name}
              onChange={(event) =>
                onChange({
                  ...definition,
                  fields: definition.fields.map((item) =>
                    item.id === field.id
                      ? { ...item, name: event.target.value }
                      : item,
                  ),
                })
              }
            />
          </label>
          <label>
            <span>Label</span>
            <input
              value={field.display.label}
              onChange={(event) =>
                onChange({
                  ...definition,
                  fields: definition.fields.map((item) =>
                    item.id === field.id
                      ? {
                          ...item,
                          display: {
                            ...item.display,
                            label: event.target.value,
                          },
                        }
                      : item,
                  ),
                })
              }
            />
          </label>
          <label>
            <span>Format</span>
            <select
              value={field.display.format ?? ""}
              onChange={(event) =>
                onChange({
                  ...definition,
                  fields: definition.fields.map((item) =>
                    item.id === field.id
                      ? {
                          ...item,
                          display: {
                            ...item.display,
                            format: (event.target.value ||
                              null) as DisplayFormat | null,
                          },
                        }
                      : item,
                  ),
                })
              }
            >
              {FORMATS.map((format) => (
                <option key={format.value} value={format.value}>
                  {format.label}
                </option>
              ))}
            </select>
          </label>
          <label className="visibility-toggle">
            <input
              type="checkbox"
              checked={field.display.visible}
              onChange={(event) =>
                onChange({
                  ...definition,
                  fields: definition.fields.map((item) =>
                    item.id === field.id
                      ? {
                          ...item,
                          display: {
                            ...item.display,
                            visible: event.target.checked,
                          },
                        }
                      : item,
                  ),
                })
              }
            />
            <span>Visible</span>
          </label>
          <button
            className="icon-button"
            aria-label={`Remove ${field.name}`}
            onClick={() =>
              onChange({
                ...definition,
                fields: definition.fields.filter(
                  (item) => item.id !== field.id,
                ),
              })
            }
            type="button"
          >
            <Trash2 />
          </button>
        </div>
      ))}
    </div>
  );
}

function SqlEditor({
  definition,
  onChange,
  autoFocus = false,
}: {
  definition: SqlDefinition;
  onChange: (value: DatasetDefinition) => void;
  autoFocus?: boolean;
}) {
  return (
    <div className="sql-editor">
      <label>
        <span>Read-only PostgreSQL query</span>
        <textarea
          autoFocus={autoFocus}
          spellCheck={false}
          value={definition.statement}
          placeholder={"SELECT\n  id,\n  created_at\nFROM public.orders"}
          onChange={(event) =>
            onChange({ ...definition, statement: event.target.value })
          }
        />
      </label>
      <div className="sql-hint">
        <ShieldCheck /> Use schema-qualified table names. Only one SELECT or
        WITH statement is accepted; validation uses EXPLAIN without ANALYZE.
      </div>
      <div className="field-editor">
        <div className="field-header">
          <span>Declared output fields</span>
          <button
            className="button secondary small"
            onClick={() =>
              onChange({
                ...definition,
                fields: [
                  ...definition.fields,
                  newSqlField(`field_${definition.fields.length + 1}`),
                ],
              })
            }
            type="button"
          >
            <Plus /> Add field
          </button>
        </div>
        {definition.fields.map((field) => (
          <div className="field-row sql-field" key={field.id}>
            <label>
              <span>SQL result name</span>
              <input
                value={field.name}
                onChange={(event) =>
                  onChange({
                    ...definition,
                    fields: definition.fields.map((item) =>
                      item.id === field.id
                        ? { ...item, name: event.target.value }
                        : item,
                    ),
                  })
                }
              />
            </label>
            <label>
              <span>Display label</span>
              <input
                value={field.display.label}
                onChange={(event) =>
                  onChange({
                    ...definition,
                    fields: definition.fields.map((item) =>
                      item.id === field.id
                        ? {
                            ...item,
                            display: {
                              ...item.display,
                              label: event.target.value,
                            },
                          }
                        : item,
                    ),
                  })
                }
              />
            </label>
            <label>
              <span>Format</span>
              <select
                value={field.display.format ?? ""}
                onChange={(event) =>
                  onChange({
                    ...definition,
                    fields: definition.fields.map((item) =>
                      item.id === field.id
                        ? {
                            ...item,
                            display: {
                              ...item.display,
                              format: (event.target.value ||
                                null) as DisplayFormat | null,
                            },
                          }
                        : item,
                    ),
                  })
                }
              >
                {FORMATS.map((format) => (
                  <option key={format.value} value={format.value}>
                    {format.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="visibility-toggle">
              <input
                type="checkbox"
                checked={field.display.visible}
                onChange={(event) =>
                  onChange({
                    ...definition,
                    fields: definition.fields.map((item) =>
                      item.id === field.id
                        ? {
                            ...item,
                            display: {
                              ...item.display,
                              visible: event.target.checked,
                            },
                          }
                        : item,
                    ),
                  })
                }
              />
              <span>Visible</span>
            </label>
            <button
              className="icon-button"
              aria-label={`Remove ${field.name}`}
              onClick={() =>
                onChange({
                  ...definition,
                  fields: definition.fields.filter(
                    (item) => item.id !== field.id,
                  ),
                })
              }
              type="button"
            >
              <Trash2 />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function JsonInspector({ definition }: { definition: DatasetDefinition }) {
  return (
    <details className="json-inspector">
      <summary>
        <span>
          <Braces /> Saved JSON definition
        </span>
        <ChevronRight />
      </summary>
      <pre>{JSON.stringify(definition, null, 2)}</pre>
    </details>
  );
}

function ValidationWorkspace({
  validation,
  dirty,
  pending,
  onValidate,
  isAdmin,
}: {
  validation: DatasetValidation | null;
  dirty: boolean;
  pending: boolean;
  onValidate: () => void;
  isAdmin: boolean;
}) {
  return (
    <div className="studio-layout">
      <main className="studio-main">
        <section className="studio-panel validation-panel">
          <header>
            <div>
              <span className="panel-icon">
                <FileCheck2 />
              </span>
              <span>
                <strong>Query Safety validation</strong>
                <small>
                  Catalog integrity and PostgreSQL planner evidence, without
                  executing the query.
                </small>
              </span>
            </div>
            <button
              className="button primary"
              disabled={dirty || pending}
              onClick={onValidate}
              type="button"
            >
              {pending ? <LoaderCircle className="spin" /> : <Play />} Validate
              draft
            </button>
          </header>
          {dirty && (
            <StudioNotice
              kind="warning"
              text="Save the current draft before validating it."
            />
          )}
          {validation ? (
            <ValidationReport validation={validation} />
          ) : (
            <div className="empty-state compact">
              <FileCheck2 />
              <strong>No validation for this revision</strong>
              <p>Run validation before previewing or publishing.</p>
            </div>
          )}
        </section>
      </main>
      <aside className="studio-side">
        <div className="studio-note">
          <ShieldCheck />
          <div>
            <strong>Read-only inspection</strong>
            <p>
              DatePipette uses EXPLAIN, catalog statistics, index visibility,
              and configured safety thresholds. It never runs EXPLAIN ANALYZE
              here.
            </p>
          </div>
        </div>
        {isAdmin && (
          <div className="studio-note">
            <AlertTriangle />
            <div>
              <strong>Administrator override</strong>
              <p>
                Blocked policy findings can be overridden with an audited reason
                through the API. Structural errors cannot be overridden.
              </p>
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}

function ValidationReport({ validation }: { validation: DatasetValidation }) {
  return (
    <div className="validation-report">
      <div className={`validation-summary ${validation.state}`}>
        <span>
          {validation.state === "valid" ? <Check /> : <AlertTriangle />}
        </span>
        <div>
          <strong>{validation.state.replaceAll("_", " ")}</strong>
          <p>
            Revision {validation.validated_revision} · policy{" "}
            {validation.policy_revision} ·{" "}
            {formatDateTime(validation.validated_at)}
          </p>
        </div>
      </div>
      {validation.plan && (
        <div className="plan-metrics">
          <Metric
            label="Estimated rows"
            value={validation.plan.estimated_rows.toLocaleString()}
          />
          <Metric
            label="Planner cost"
            value={validation.plan.total_cost.toLocaleString()}
          />
          <Metric label="Joins" value={String(validation.plan.join_count)} />
          <Metric
            label="Largest scan"
            value={validation.plan.max_sequential_scan_rows.toLocaleString()}
          />
        </div>
      )}
      <div className="finding-list">
        {validation.findings.length === 0 ? (
          <p className="all-clear">
            <Check /> No safety findings.
          </p>
        ) : (
          validation.findings.map((finding, index) => (
            <article
              key={`${finding.code}-${index}`}
              className={finding.severity}
            >
              <AlertTriangle />
              <div>
                <strong>{labelCode(finding.code)}</strong>
                <p>
                  {finding.observed_value !== null
                    ? `Observed ${finding.observed_value.toLocaleString()}`
                    : "Review the referenced source definition."}
                  {finding.threshold_value !== null
                    ? ` · threshold ${finding.threshold_value.toLocaleString()}`
                    : ""}
                </p>
              </div>
              <span>{finding.severity}</span>
            </article>
          ))
        )}
      </div>
      {validation.recommendations.length > 0 && (
        <div className="recommendation-list">
          <strong>Recommended review</strong>
          {validation.recommendations.map((item, index) => (
            <p key={`${item.code}-${index}`}>
              <ChevronRight /> {labelCode(item.code)}
              {item.column_names.length
                ? `: ${item.column_names.join(", ")}`
                : ""}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

function PreviewWorkspace({
  preview,
  disabled,
  pending,
  onPreview,
}: {
  preview: DatasetPreview | null;
  disabled: boolean;
  pending: boolean;
  onPreview: () => void;
}) {
  return (
    <section className="studio-panel preview-panel">
      <header>
        <div>
          <span className="panel-icon">
            <Eye />
          </span>
          <span>
            <strong>Live bounded preview</strong>
            <small>
              Reads PostgreSQL only for this explicit preview; no snapshot is
              created.
            </small>
          </span>
        </div>
        <button
          className="button primary"
          disabled={disabled || pending}
          onClick={onPreview}
          type="button"
        >
          {pending ? <LoaderCircle className="spin" /> : <Play />} Preview 100
          rows
        </button>
      </header>
      {disabled && (
        <StudioNotice
          kind="warning"
          text="Save and successfully validate this exact revision first."
        />
      )}
      {preview ? (
        <>
          <div className="preview-meta">
            <span>{preview.returned_rows} rows returned</span>
            <span>Policy revision {preview.policy_revision}</span>
            {preview.truncated && (
              <span className="warning">
                Bounded preview ·{" "}
                {preview.truncation_reason?.replaceAll("_", " ")}
              </span>
            )}
          </div>
          <div className="studio-table-scroll">
            <table>
              <thead>
                <tr>
                  {preview.columns.map((column) => (
                    <th key={column.field_id}>
                      {column.name}
                      <small>{column.data_type}</small>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((row, index) => (
                  <tr key={index}>
                    {preview.columns.map((column) => (
                      <td key={column.field_id}>
                        {renderValue(row[column.name])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <div className="empty-state compact">
          <Eye />
          <strong>Preview is intentionally explicit</strong>
          <p>Validation does not execute your business query.</p>
        </div>
      )}
    </section>
  );
}

function PublishWorkspace({
  datasetId,
  dataset,
  dirty,
  valid,
  impact,
  run,
  impactPending,
  publishPending,
  onImpact,
  onPublish,
}: {
  datasetId: string;
  dataset: ApiSchemas["DatasetResponse"];
  dirty: boolean;
  valid: boolean;
  impact: DatasetImpact | null;
  run: RefreshRun | null;
  impactPending: boolean;
  publishPending: boolean;
  onImpact: () => void;
  onPublish: () => void;
}) {
  const ready = !dirty && valid && impact?.ready_for_publication;
  return (
    <div className="studio-layout">
      <main className="studio-main">
        <section className="studio-panel publish-panel">
          <header>
            <div>
              <span className="panel-icon">
                <RefreshCw />
              </span>
              <span>
                <strong>
                  {dataset.active_version_id
                    ? "Publish changes"
                    : "First publication"}
                </strong>
                <small>
                  Builds an immutable Parquet snapshot in the background, then
                  activates it atomically.
                </small>
              </span>
            </div>
          </header>
          <div className="publish-steps">
            <article className={valid && !dirty ? "done" : ""}>
              <span>{valid && !dirty ? <Check /> : "1"}</span>
              <div>
                <strong>Validated revision</strong>
                <p>
                  {valid && !dirty
                    ? "The saved draft passed Query Safety."
                    : "Save and validate the exact draft revision."}
                </p>
              </div>
            </article>
            <article
              className={
                impact
                  ? impact.ready_for_publication
                    ? "done"
                    : "blocked"
                  : ""
              }
            >
              <span>{impact?.ready_for_publication ? <Check /> : "2"}</span>
              <div>
                <strong>Schema impact</strong>
                <p>
                  {impact
                    ? `${impact.changes.length} changes · ${impact.breaking_change_count} breaking`
                    : "Compare stable fields with the active published contract."}
                </p>
              </div>
              <button
                className="button secondary small"
                disabled={dirty || !valid || impactPending}
                onClick={onImpact}
                type="button"
              >
                {impactPending ? (
                  <LoaderCircle className="spin" />
                ) : (
                  <FileCheck2 />
                )}{" "}
                Analyze
              </button>
            </article>
            <article
              className={
                run?.state === "succeeded"
                  ? "done"
                  : run?.state === "failed"
                    ? "blocked"
                    : ""
              }
            >
              <span>{run?.state === "succeeded" ? <Check /> : "3"}</span>
              <div>
                <strong>Build and activate snapshot</strong>
                <p>
                  {run
                    ? `Run ${run.state}${run.actual_rows !== null ? ` · ${run.actual_rows.toLocaleString()} rows` : ""}`
                    : "Readers keep the last good snapshot until this completes."}
                </p>
              </div>
              <button
                className="button primary small"
                disabled={
                  !ready ||
                  publishPending ||
                  Boolean(run && ["queued", "running"].includes(run.state))
                }
                onClick={onPublish}
                type="button"
              >
                {publishPending ||
                (run && ["queued", "running"].includes(run.state)) ? (
                  <LoaderCircle className="spin" />
                ) : (
                  <RefreshCw />
                )}{" "}
                {dataset.active_version_id
                  ? "Publish update"
                  : "Publish dataset"}
              </button>
            </article>
          </div>
          {impact && !impact.ready_for_publication && (
            <StudioNotice
              kind="danger"
              text="Breaking changes affect saved dependencies. Resolve them before publication."
            />
          )}
          {run?.error_code && (
            <StudioNotice
              kind="danger"
              text={`Publication failed safely: ${run.error_code}. The previous snapshot remains active.`}
            />
          )}
          {run?.state === "succeeded" && (
            <div className="publish-success">
              <Check />
              <div>
                <strong>Snapshot activated</strong>
                <p>
                  The published table is ready. A fresh editable draft has been
                  created automatically.
                </p>
              </div>
              <Link
                className="button secondary small"
                to={`/datasets/${datasetId}/table`}
              >
                Open data <ChevronRight />
              </Link>
            </div>
          )}
        </section>
      </main>
      <aside className="studio-side">
        <SchedulePanel
          datasetId={datasetId}
          published={Boolean(
            dataset.active_version_id || run?.state === "succeeded",
          )}
        />
        <div className="studio-note">
          <ShieldCheck />
          <div>
            <strong>Last-known-good guarantee</strong>
            <p>
              A failed refresh never replaces a working snapshot. Readers see
              stale data with safe error metadata.
            </p>
          </div>
        </div>
      </aside>
    </div>
  );
}

function SchedulePanel({
  datasetId,
  published,
}: {
  datasetId: string;
  published: boolean;
}) {
  const queryClient = useQueryClient();
  const schedule = useQuery({
    queryKey: queryKeys.refreshSchedule(datasetId),
    queryFn: () => getRefreshSchedule(datasetId),
    enabled: published,
    retry: false,
  });
  const [kind, setKind] = useState<"interval" | "daily">("interval");
  const [interval, setInterval] = useState(60);
  const [dailyTime, setDailyTime] = useState("06:00");
  const [enabled, setEnabled] = useState(false);
  const initialized = useRef(false);
  useEffect(() => {
    if (!initialized.current && schedule.data) {
      initialized.current = true;
      setKind(schedule.data.kind);
      setInterval(schedule.data.interval_minutes ?? 60);
      setDailyTime(schedule.data.daily_time?.slice(0, 5) ?? "06:00");
      setEnabled(schedule.data.enabled);
    }
  }, [schedule.data]);
  const save = useMutation({
    mutationFn: () =>
      replaceRefreshSchedule(
        datasetId,
        {
          kind,
          interval_minutes: kind === "interval" ? interval : null,
          daily_time: kind === "daily" ? dailyTime : null,
          enabled,
        } as RefreshScheduleInput,
        schedule.data?.revision,
      ),
    onSuccess: (value) =>
      queryClient.setQueryData(queryKeys.refreshSchedule(datasetId), value),
  });
  if (!published)
    return (
      <section className="schedule-card disabled">
        <Clock3 />
        <strong>Refresh schedule</strong>
        <p>Available after the first successful publication.</p>
      </section>
    );
  const absent =
    schedule.error instanceof DatePipetteApiError &&
    schedule.error.status === 404;
  return (
    <section className="schedule-card">
      <header>
        <div>
          <Clock3 />
          <span>
            <strong>Refresh schedule</strong>
            <small>{schedule.data?.timezone ?? "Installation timezone"}</small>
          </span>
        </div>
        <label className="switch">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          <span />
        </label>
      </header>
      <label>
        <span>Cadence</span>
        <select
          value={kind}
          onChange={(event) =>
            setKind(event.target.value as "interval" | "daily")
          }
        >
          <option value="interval">Fixed interval</option>
          <option value="daily">Daily time</option>
        </select>
      </label>
      {kind === "interval" ? (
        <label>
          <span>Every</span>
          <select
            value={interval}
            onChange={(event) => setInterval(Number(event.target.value))}
          >
            <option value={15}>15 minutes</option>
            <option value={60}>1 hour</option>
            <option value={360}>6 hours</option>
            <option value={1440}>24 hours</option>
            <option value={10080}>7 days</option>
          </select>
        </label>
      ) : (
        <label>
          <span>At</span>
          <input
            type="time"
            value={dailyTime}
            onChange={(event) => setDailyTime(event.target.value)}
          />
        </label>
      )}
      {schedule.data?.next_run_at && (
        <p>Next run {formatDateTime(schedule.data.next_run_at)}</p>
      )}
      {schedule.error && !absent && (
        <InlineError message={problemMessage(schedule.error)} />
      )}
      <button
        className="button secondary small wide"
        disabled={save.isPending}
        onClick={() => save.mutate()}
        type="button"
      >
        {save.isPending ? <LoaderCircle className="spin" /> : <Save />}{" "}
        {absent ? "Create schedule" : "Save schedule"}
      </button>
      {save.isError && <InlineError message={problemMessage(save.error)} />}
    </section>
  );
}

function StudioNotice({
  kind,
  text,
}: {
  kind: "warning" | "danger";
  text: string;
}) {
  return (
    <div className={`studio-notice ${kind}`}>
      <AlertTriangle /> {text}
    </div>
  );
}
function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
function labelCode(value: string): string {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toLocaleUpperCase() + words.slice(1);
}
function renderValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
