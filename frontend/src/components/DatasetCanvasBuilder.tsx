import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  applyNodeChanges,
  type Edge,
  type Node,
  type NodeChange,
  type Viewport,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useQuery } from "@tanstack/react-query";
import {
  Columns3,
  Database,
  GitBranch,
  LayoutDashboard,
  ListChecks,
  LoaderCircle,
  Plus,
  Redo2,
  Save,
  Settings2,
  Sparkles,
  Trash2,
  Undo2,
  WandSparkles,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getCatalogRelation,
  listCatalogRelations,
  listCatalogSchemas,
  problemMessage,
  suggestCatalogJoins,
  type CatalogJoinSuggestion,
  type CatalogRelation,
} from "../lib/api";
import {
  appendManualJoinedRelation,
  appendRelationFromSuggestion,
  autoLayoutCanvas,
  canvasNodeId,
  catalogTypesCompatible,
  ensureCanvas,
  removeJoinSubtree,
  updateCanvasNodePositions,
  updateCanvasViewport,
} from "../lib/canvasDefinition";
import { useI18n } from "../lib/i18n";
import { randomUuid } from "../lib/id";
import { queryKeys } from "../lib/query";
import {
  appendVisualField,
  type DisplayFormat,
  type VisualDefinition,
} from "../lib/studioDefinition";
import { InlineError } from "./Status";

type CanvasNode = Node<{ label: React.ReactNode; kind: string }>;
type Selection = string | "add-table";
type SuggestedJoin = CatalogJoinSuggestion & { candidateId: string };

const FORMATS: { value: "" | DisplayFormat; label: string }[] = [
  { value: "", label: "Automatic" },
  { value: "date_dd_mm_yyyy", label: "DD-MM-YYYY" },
  { value: "date_yyyy_mm_dd", label: "YYYY-MM-DD" },
  { value: "datetime_dd_mm_yyyy_hh_mm", label: "DD-MM-YYYY HH:mm" },
  { value: "number_integer", label: "Whole number" },
  { value: "number_decimal_2", label: "2 decimal places" },
  { value: "percent", label: "Percent" },
];

export function DatasetCanvasBuilder({
  connectionId,
  definition,
  onChange,
}: {
  connectionId: string;
  definition: VisualDefinition;
  onChange: (definition: VisualDefinition) => void;
}) {
  const { t } = useI18n();
  const past = useRef<VisualDefinition[]>([]);
  const future = useRef<VisualDefinition[]>([]);
  const [historyRevision, setHistoryRevision] = useState(0);
  const [selection, setSelection] = useState<Selection>(canvasNodeId.output);
  const stableDefinition = useMemo(() => ensureCanvas(definition), [definition]);
  const [nodes, setNodes] = useState<CanvasNode[]>(() =>
    buildNodes(stableDefinition, t),
  );

  useEffect(() => {
    setNodes(buildNodes(stableDefinition, t));
  }, [stableDefinition, t]);

  const commit = useCallback(
    (next: VisualDefinition, record = true) => {
      if (JSON.stringify(next) === JSON.stringify(definition)) return;
      if (record) {
        past.current.push(definition);
        if (past.current.length > 50) past.current.shift();
        future.current = [];
      }
      onChange(next);
      setHistoryRevision((value) => value + 1);
    },
    [definition, onChange],
  );

  const undo = useCallback(() => {
    const previous = past.current.pop();
    if (!previous) return;
    future.current.push(definition);
    onChange(previous);
    setHistoryRevision((value) => value + 1);
  }, [definition, onChange]);

  const redo = useCallback(() => {
    const next = future.current.pop();
    if (!next) return;
    past.current.push(definition);
    onChange(next);
    setHistoryRevision((value) => value + 1);
  }, [definition, onChange]);

  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "z")
        return;
      const target = event.target as HTMLElement | null;
      if (target?.matches("input, textarea, select, [contenteditable='true']"))
        return;
      event.preventDefault();
      if (event.shiftKey) redo();
      else undo();
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [redo, undo]);

  const edges = useMemo(() => buildEdges(stableDefinition, t), [stableDefinition, t]);
  const onNodesChange = useCallback(
    (changes: NodeChange<CanvasNode>[]) =>
      setNodes((current) => applyNodeChanges(changes, current)),
    [],
  );
  const onNodeDragStop = useCallback(
    (_event: MouseEvent | TouchEvent, moved: CanvasNode) => {
      commit(
        updateCanvasNodePositions(stableDefinition, [
          { id: moved.id, x: moved.position.x, y: moved.position.y },
        ]),
      );
    },
    [commit, stableDefinition],
  );
  const onMoveEnd = useCallback(
    (event: MouseEvent | TouchEvent | null, viewport: Viewport) => {
      if (!definition.canvas && event === null) return;
      const current = stableDefinition.canvas?.viewport ?? { x: 0, y: 0, zoom: 1 };
      if (
        Math.abs(current.x - viewport.x) < 0.5 &&
        Math.abs(current.y - viewport.y) < 0.5 &&
        Math.abs(current.zoom - viewport.zoom) < 0.001
      )
        return;
      commit(updateCanvasViewport(stableDefinition, viewport), false);
    },
    [commit, stableDefinition],
  );
  const removeJoin = useCallback(
    (joinId: string) => {
      commit(removeJoinSubtree(stableDefinition, joinId));
      setSelection(canvasNodeId.output);
    },
    [commit, stableDefinition],
  );

  return (
    <div className="dataset-canvas-shell" data-history={historyRevision}>
      <header className="canvas-toolbar">
        <div>
          <span className="canvas-live-dot" />
          <strong>{t("Canvas Builder")}</strong>
          <small>{t("Drag nodes to shape the model. The same safe JSON draft stays authoritative.")}</small>
        </div>
        <nav aria-label={t("Canvas actions")}>
          <button
            className="icon-button"
            type="button"
            disabled={!past.current.length}
            onClick={undo}
            aria-label={t("Undo")}
            title={`${t("Undo")} · Ctrl/⌘ Z`}
          >
            <Undo2 />
          </button>
          <button
            className="icon-button"
            type="button"
            disabled={!future.current.length}
            onClick={redo}
            aria-label={t("Redo")}
            title={`${t("Redo")} · Ctrl/⌘ Shift Z`}
          >
            <Redo2 />
          </button>
          <button
            className="button secondary small"
            type="button"
            onClick={() => commit(autoLayoutCanvas(stableDefinition))}
          >
            <WandSparkles /> {t("Auto layout")}
          </button>
          <button
            className="button primary small"
            type="button"
            disabled={stableDefinition.source.relations.length >= 10}
            onClick={() => setSelection("add-table")}
          >
            <Plus /> {t("Add table")}
          </button>
        </nav>
      </header>
      <div className="dataset-canvas-workspace">
        <div className="dataset-flow" aria-label={t("Dataset node canvas")}>
          <ReactFlow<CanvasNode>
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onNodeDragStop={onNodeDragStop}
            onMoveEnd={onMoveEnd}
            onNodeClick={(_event, node) => setSelection(node.id)}
            onEdgeClick={(_event, edge) => {
              const joinId = edge.data?.joinId;
              if (typeof joinId === "string") setSelection(canvasNodeId.join(joinId));
            }}
            onEdgesDelete={(deleted) => {
              const joinId = deleted.find((edge) => edge.data?.joinId)?.data?.joinId;
              if (typeof joinId === "string") removeJoin(joinId);
            }}
            defaultViewport={stableDefinition.canvas?.viewport ?? { x: 0, y: 0, zoom: 1 }}
            minZoom={0.2}
            maxZoom={2}
            fitView={!definition.canvas}
            fitViewOptions={{ padding: 0.18 }}
            deleteKeyCode={["Backspace", "Delete"]}
            nodesConnectable={false}
            nodesFocusable
            edgesFocusable
          >
            <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} />
            <MiniMap
              pannable
              zoomable
              nodeColor={(node) => nodeColor(String(node.data?.kind ?? ""))}
              maskColor="rgba(245, 245, 242, .72)"
            />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
        <aside className="canvas-inspector" aria-label={t("Selected node settings")}>
          <CanvasInspector
            connectionId={connectionId}
            definition={stableDefinition}
            selection={selection}
            onSelect={setSelection}
            onChange={commit}
            onRemoveJoin={removeJoin}
          />
        </aside>
      </div>
    </div>
  );
}

function CanvasInspector({
  connectionId,
  definition,
  selection,
  onSelect,
  onChange,
  onRemoveJoin,
}: {
  connectionId: string;
  definition: VisualDefinition;
  selection: Selection;
  onSelect: (selection: Selection) => void;
  onChange: (definition: VisualDefinition) => void;
  onRemoveJoin: (joinId: string) => void;
}) {
  const { t } = useI18n();
  if (selection === "add-table")
    return (
      <AddTableInspector
        connectionId={connectionId}
        definition={definition}
        onCancel={() => onSelect(canvasNodeId.output)}
        onAdd={(next) => {
          onChange(next);
          const latest = next.source.relations.at(-1);
          if (latest) onSelect(canvasNodeId.relation(latest.id));
        }}
      />
    );
  if (selection === canvasNodeId.select)
    return (
      <SelectColumnsInspector
        connectionId={connectionId}
        definition={definition}
        onChange={onChange}
      />
    );
  if (selection === canvasNodeId.format)
    return <FormatInspector definition={definition} onChange={onChange} />;
  if (selection === canvasNodeId.output)
    return (
      <InspectorFrame icon={<Save />} title={t("Dataset Output")} eyebrow={t("Final node")}>
        <div className="canvas-output-summary">
          <strong>{t("Ready for the governed workflow")}</strong>
          <p>{t("Save the draft, then validate, preview, and publish it with the existing Dataset Studio steps.")}</p>
          <dl>
            <div><dt>{t("Tables")}</dt><dd>{definition.source.relations.length}</dd></div>
            <div><dt>{t("Joins")}</dt><dd>{definition.joins?.length ?? 0}</dd></div>
            <div><dt>{t("Output fields")}</dt><dd>{definition.fields.length}</dd></div>
          </dl>
        </div>
      </InspectorFrame>
    );
  if (selection.startsWith("relation:")) {
    const relationId = selection.slice("relation:".length);
    const relation = definition.source.relations.find((item) => item.id === relationId);
    if (!relation) return null;
    const incoming = definition.joins?.find((join) => join.right_relation_id === relation.id);
    return (
      <InspectorFrame icon={<Database />} title={relation.alias} eyebrow={t("PostgreSQL Table")}>
        <p className="canvas-source-path">{relation.schema}.{relation.name}</p>
        <label>
          <span>{t("Relation alias")}</span>
          <input
            value={relation.alias}
            onChange={(event) =>
              onChange({
                ...definition,
                source: {
                  relations: definition.source.relations.map((item) =>
                    item.id === relation.id ? { ...item, alias: event.target.value } : item,
                  ),
                },
              })
            }
          />
        </label>
        <p className="canvas-inspector-hint">
          {t("This node reads catalog metadata only while editing. Rows are queried only by preview or publication.")}
        </p>
        {incoming && (
          <button className="button danger wide" type="button" onClick={() => onRemoveJoin(incoming.id)}>
            <Trash2 /> {t("Remove table and downstream joins")}
          </button>
        )}
      </InspectorFrame>
    );
  }
  if (selection.startsWith("join:")) {
    const joinId = selection.slice("join:".length);
    const join = definition.joins?.find((item) => item.id === joinId);
    return join ? (
      <JoinInspector
        connectionId={connectionId}
        definition={definition}
        join={join}
        onChange={onChange}
        onRemove={() => onRemoveJoin(join.id)}
      />
    ) : null;
  }
  return (
    <InspectorFrame icon={<Settings2 />} title={t("Node settings")} eyebrow={t("Canvas")}>
      <p>{t("Select a node to configure it.")}</p>
    </InspectorFrame>
  );
}

function AddTableInspector({
  connectionId,
  definition,
  onAdd,
  onCancel,
}: {
  connectionId: string;
  definition: VisualDefinition;
  onAdd: (definition: VisualDefinition) => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const [schema, setSchema] = useState("");
  const [relationName, setRelationName] = useState("");
  const [parentId, setParentId] = useState(definition.source.relations[0]?.id ?? "");
  const [leftColumn, setLeftColumn] = useState("");
  const [rightColumn, setRightColumn] = useState("");
  const [suggestions, setSuggestions] = useState<SuggestedJoin[]>([]);
  const [finding, setFinding] = useState("");
  const [loadingSuggestions, setLoadingSuggestions] = useState(false);
  const schemas = useQuery({
    queryKey: queryKeys.catalogSchemas(connectionId),
    queryFn: () => listCatalogSchemas(connectionId),
  });
  const relations = useQuery({
    queryKey: queryKeys.catalogRelations(connectionId, schema),
    queryFn: () => listCatalogRelations(connectionId, schema),
    enabled: Boolean(schema),
  });
  const detail = useQuery({
    queryKey: queryKeys.catalogRelation(connectionId, schema, relationName),
    queryFn: () => getCatalogRelation(connectionId, schema, relationName),
    enabled: Boolean(schema && relationName),
  });
  const parent = definition.source.relations.find((item) => item.id === parentId);
  const parentDetail = useQuery({
    queryKey: queryKeys.catalogRelation(
      connectionId,
      parent?.schema ?? "",
      parent?.name ?? "",
    ),
    queryFn: () => getCatalogRelation(connectionId, parent!.schema, parent!.name),
    enabled: Boolean(parent),
  });
  useEffect(() => {
    if (!schema && schemas.data?.data[0]) setSchema(schemas.data.data[0].name);
  }, [schema, schemas.data]);
  useEffect(() => {
    setRelationName("");
    setSuggestions([]);
  }, [schema]);
  useEffect(() => {
    setSuggestions([]);
    setFinding("");
  }, [relationName]);
  const selectedLeft = parentDetail.data?.columns.find((column) => column.name === leftColumn);
  const compatibleRight = detail.data?.columns.filter(
    (column) => !selectedLeft || catalogTypesCompatible(selectedLeft.data_type, column.data_type),
  );
  async function findSuggestions() {
    if (!detail.data) return;
    setFinding("");
    setLoadingSuggestions(true);
    const candidateId = randomUuid();
    try {
      const found = await suggestCatalogJoins(connectionId, [
        ...definition.source.relations.map((item) => ({
          id: item.id,
          schema: item.schema,
          name: item.name,
        })),
        { id: candidateId, schema: detail.data.schema_name, name: detail.data.name },
      ]);
      const mapped = found.map((item) => ({ ...item, candidateId }));
      setSuggestions(mapped);
      if (!mapped.length) setFinding(t("No visible foreign key was found. Configure a manual compatible-column join below."));
    } catch (error) {
      setFinding(problemMessage(error));
    } finally {
      setLoadingSuggestions(false);
    }
  }
  return (
    <InspectorFrame icon={<Plus />} title={t("Add PostgreSQL table")} eyebrow={t("New source node")}>
      <label><span>{t("Schema")}</span><select value={schema} onChange={(event) => setSchema(event.target.value)}><option value="">{t("Select schema")}</option>{schemas.data?.data.map((item) => <option key={item.name}>{item.name}</option>)}</select></label>
      <label><span>{t("Table or view")}</span><select value={relationName} onChange={(event) => setRelationName(event.target.value)}><option value="">{t("Select relation")}</option>{relations.data?.data.map((item) => <option key={item.name} value={item.name}>{item.name} · {item.kind}</option>)}</select></label>
      {detail.data && (
        <>
          <div className="canvas-relation-preview">
            <Database />
            <div><strong>{detail.data.schema_name}.{detail.data.name}</strong><small>{t("{count} columns", { count: detail.data.columns.length })}</small></div>
          </div>
          <button className="button primary wide" type="button" disabled={loadingSuggestions} onClick={() => void findSuggestions()}>
            {loadingSuggestions ? <LoaderCircle className="spin" /> : <Sparkles />} {t("Find foreign-key joins")}
          </button>
        </>
      )}
      {suggestions.map((suggestion) => (
        <button
          className="canvas-suggestion"
          type="button"
          key={`${suggestion.foreign_key_name}:${suggestion.source_relation_id}`}
          onClick={() => onAdd(appendRelationFromSuggestion(definition, detail.data!, suggestion, suggestion.candidateId))}
        >
          <GitBranch />
          <span><strong>{suggestion.foreign_key_name}</strong><small>{suggestion.conditions.map((item) => `${item.source_column} = ${item.target_column}`).join(", ")}</small></span>
        </button>
      ))}
      {finding && <InlineError message={finding} />}
      {detail.data && (
        <details className="manual-join" open={!suggestions.length && Boolean(finding)}>
          <summary>{t("Configure manual join")}</summary>
          <label><span>{t("Existing table")}</span><select value={parentId} onChange={(event) => { setParentId(event.target.value); setLeftColumn(""); setRightColumn(""); }}>{definition.source.relations.map((item) => <option value={item.id} key={item.id}>{item.alias}</option>)}</select></label>
          <label><span>{t("Existing column")}</span><select value={leftColumn} onChange={(event) => { setLeftColumn(event.target.value); setRightColumn(""); }}><option value="">{t("Select column")}</option>{parentDetail.data?.columns.map((column) => <option value={column.name} key={column.name}>{column.name} · {column.data_type}</option>)}</select></label>
          <label><span>{t("New table column")}</span><select value={rightColumn} onChange={(event) => setRightColumn(event.target.value)} disabled={!leftColumn}><option value="">{t("Select compatible column")}</option>{compatibleRight?.map((column) => <option value={column.name} key={column.name}>{column.name} · {column.data_type}</option>)}</select></label>
          <button className="button secondary wide" type="button" disabled={!leftColumn || !rightColumn} onClick={() => onAdd(appendManualJoinedRelation(definition, detail.data!, parentId, leftColumn, rightColumn))}><GitBranch /> {t("Add manual LEFT JOIN")}</button>
        </details>
      )}
      <button className="button ghost wide" type="button" onClick={onCancel}>{t("Cancel")}</button>
    </InspectorFrame>
  );
}

function JoinInspector({
  connectionId,
  definition,
  join,
  onChange,
  onRemove,
}: {
  connectionId: string;
  definition: VisualDefinition;
  join: NonNullable<VisualDefinition["joins"]>[number];
  onChange: (definition: VisualDefinition) => void;
  onRemove: () => void;
}) {
  const { t } = useI18n();
  const left = definition.source.relations.find((item) => item.id === join.left_relation_id)!;
  const right = definition.source.relations.find((item) => item.id === join.right_relation_id)!;
  const leftDetail = useQuery({ queryKey: queryKeys.catalogRelation(connectionId, left.schema, left.name), queryFn: () => getCatalogRelation(connectionId, left.schema, left.name) });
  const rightDetail = useQuery({ queryKey: queryKeys.catalogRelation(connectionId, right.schema, right.name), queryFn: () => getCatalogRelation(connectionId, right.schema, right.name) });
  function replaceJoin(next: typeof join) {
    onChange({ ...definition, joins: definition.joins?.map((item) => item.id === join.id ? next : item) });
  }
  return (
    <InspectorFrame icon={<GitBranch />} title={`${left.alias} → ${right.alias}`} eyebrow={t("Join node")}>
      <label><span>{t("Join type")}</span><select value={join.type} onChange={(event) => replaceJoin({ ...join, type: event.target.value as "inner" | "left" })}><option value="left">LEFT JOIN</option><option value="inner">INNER JOIN</option></select></label>
      <div className="canvas-condition-list">
        {join.conditions.map((condition, index) => (
          <div className="canvas-condition" key={`${index}:${condition.left_column}:${condition.right_column}`}>
            <label><span>{left.alias}</span><select value={condition.left_column} onChange={(event) => replaceJoin({ ...join, conditions: join.conditions.map((item, itemIndex) => itemIndex === index ? { ...item, left_column: event.target.value } : item) })}>{leftDetail.data?.columns.map((column) => <option key={column.name}>{column.name}</option>)}</select></label>
            <span>=</span>
            <label><span>{right.alias}</span><select value={condition.right_column} onChange={(event) => replaceJoin({ ...join, conditions: join.conditions.map((item, itemIndex) => itemIndex === index ? { ...item, right_column: event.target.value } : item) })}>{rightDetail.data?.columns.map((column) => <option key={column.name}>{column.name}</option>)}</select></label>
            {join.conditions.length > 1 && <button className="icon-button" type="button" aria-label={t("Remove condition")} onClick={() => replaceJoin({ ...join, conditions: join.conditions.filter((_, itemIndex) => itemIndex !== index) })}><Trash2 /></button>}
          </div>
        ))}
      </div>
      {join.conditions.length < 8 && leftDetail.data?.columns[0] && rightDetail.data?.columns[0] && (
        <button className="button secondary wide" type="button" onClick={() => replaceJoin({ ...join, conditions: [...join.conditions, { left_column: leftDetail.data!.columns[0].name, right_column: rightDetail.data!.columns[0].name }] })}><Plus /> {t("Add condition")}</button>
      )}
      <p className="canvas-inspector-hint">{t("Validation checks that every join column still exists and that the complete graph remains authorized.")}</p>
      <button className="button danger wide" type="button" onClick={onRemove}><Trash2 /> {t("Delete join edge")}</button>
    </InspectorFrame>
  );
}

function SelectColumnsInspector({ connectionId, definition, onChange }: { connectionId: string; definition: VisualDefinition; onChange: (definition: VisualDefinition) => void }) {
  const { t } = useI18n();
  return (
    <InspectorFrame icon={<ListChecks />} title={t("Select Columns")} eyebrow={t("Projection node")}>
      <p className="canvas-inspector-hint">{t("Choose detailed source columns. Aggregations belong to charts, not datasets.")}</p>
      {definition.source.relations.map((relation) => <CanvasRelationColumns key={relation.id} connectionId={connectionId} relation={relation} definition={definition} onChange={onChange} />)}
    </InspectorFrame>
  );
}

function CanvasRelationColumns({ connectionId, relation, definition, onChange }: { connectionId: string; relation: VisualDefinition["source"]["relations"][number]; definition: VisualDefinition; onChange: (definition: VisualDefinition) => void }) {
  const { t } = useI18n();
  const detail = useQuery({ queryKey: queryKeys.catalogRelation(connectionId, relation.schema, relation.name), queryFn: () => getCatalogRelation(connectionId, relation.schema, relation.name) });
  const selected = new Set(definition.fields.filter((field) => field.source.relation_id === relation.id).map((field) => field.source.column));
  return (
    <section className="canvas-column-group">
      <header><strong>{relation.alias}</strong><small>{t("{count} selected", { count: selected.size })}</small></header>
      {detail.isError && <InlineError message={problemMessage(detail.error)} />}
      {detail.data?.columns.map((column) => <label key={column.name}><input type="checkbox" checked={selected.has(column.name)} onChange={(event) => onChange(event.target.checked ? appendVisualField(definition, relation.id, column.name) : { ...definition, fields: definition.fields.filter((field) => !(field.source.relation_id === relation.id && field.source.column === column.name)) })} /><span><strong>{column.name}</strong><small>{column.data_type}</small></span></label>)}
    </section>
  );
}

function FormatInspector({ definition, onChange }: { definition: VisualDefinition; onChange: (definition: VisualDefinition) => void }) {
  const { t } = useI18n();
  return (
    <InspectorFrame icon={<Columns3 />} title={t("Rename & Format")} eyebrow={t("Presentation node")}>
      <p className="canvas-inspector-hint">{t("Output names are stable API fields. Labels and formats control presentation only.")}</p>
      <div className="canvas-format-list">
        {definition.fields.map((field) => (
          <article key={field.id}>
            <strong>{field.source.column}</strong>
            <label><span>{t("Output name")}</span><input value={field.name} onChange={(event) => onChange({ ...definition, fields: definition.fields.map((item) => item.id === field.id ? { ...item, name: event.target.value } : item) })} /></label>
            <label><span>{t("Label")}</span><input value={field.display.label} onChange={(event) => onChange({ ...definition, fields: definition.fields.map((item) => item.id === field.id ? { ...item, display: { ...item.display, label: event.target.value } } : item) })} /></label>
            <label><span>{t("Format")}</span><select value={field.display.format ?? ""} onChange={(event) => onChange({ ...definition, fields: definition.fields.map((item) => item.id === field.id ? { ...item, display: { ...item.display, format: (event.target.value || null) as DisplayFormat | null } } : item) })}>{FORMATS.map((format) => <option value={format.value} key={format.value}>{t(format.label)}</option>)}</select></label>
            <label className="canvas-visible"><input type="checkbox" checked={field.display.visible} onChange={(event) => onChange({ ...definition, fields: definition.fields.map((item) => item.id === field.id ? { ...item, display: { ...item.display, visible: event.target.checked } } : item) })} /><span>{t("Visible")}</span></label>
          </article>
        ))}
      </div>
    </InspectorFrame>
  );
}

function InspectorFrame({ icon, eyebrow, title, children }: { icon: React.ReactNode; eyebrow: string; title: string; children: React.ReactNode }) {
  return <div className="canvas-inspector-frame"><header><span>{icon}</span><div><small>{eyebrow}</small><strong>{title}</strong></div></header><div className="canvas-inspector-body">{children}</div></div>;
}

function buildNodes(definition: VisualDefinition, t: ReturnType<typeof useI18n>["t"]): CanvasNode[] {
  const positions = new Map((ensureCanvas(definition).canvas?.nodes ?? []).map((node) => [node.id, { x: node.x, y: node.y }]));
  const relationNodes: CanvasNode[] = definition.source.relations.map((relation, index) => ({
    id: canvasNodeId.relation(relation.id),
    position: positions.get(canvasNodeId.relation(relation.id))!,
    data: { kind: "relation", label: <NodeLabel icon={<Database />} eyebrow={index === 0 ? t("Root table") : t("Joined table")} title={relation.alias} detail={`${relation.schema}.${relation.name}`} badge={t("{count} fields", { count: definition.fields.filter((field) => field.source.relation_id === relation.id).length })} /> },
    className: "dataset-node relation-node",
    deletable: false,
  }));
  const joinNodes: CanvasNode[] = (definition.joins ?? []).map((join) => {
    const left = definition.source.relations.find((item) => item.id === join.left_relation_id)?.alias;
    const right = definition.source.relations.find((item) => item.id === join.right_relation_id)?.alias;
    return { id: canvasNodeId.join(join.id), position: positions.get(canvasNodeId.join(join.id))!, data: { kind: "join", label: <NodeLabel icon={<GitBranch />} eyebrow={join.type === "left" ? "LEFT JOIN" : "INNER JOIN"} title={`${left} + ${right}`} detail={join.conditions.map((item) => `${item.left_column} = ${item.right_column}`).join(", ")} badge={t("{count} conditions", { count: join.conditions.length })} /> }, className: "dataset-node join-node", deletable: false };
  });
  const pipeline: CanvasNode[] = [
    { id: canvasNodeId.select, position: positions.get(canvasNodeId.select)!, data: { kind: "select", label: <NodeLabel icon={<ListChecks />} eyebrow={t("Transform")} title={t("Select Columns")} detail={t("Detailed rows only")} badge={t("{count} selected", { count: definition.fields.length })} /> }, className: "dataset-node transform-node", deletable: false },
    { id: canvasNodeId.format, position: positions.get(canvasNodeId.format)!, data: { kind: "format", label: <NodeLabel icon={<Columns3 />} eyebrow={t("Transform")} title={t("Rename & Format")} detail={t("Labels and display types")} badge={t("Non-destructive")} /> }, className: "dataset-node transform-node", deletable: false },
    { id: canvasNodeId.output, position: positions.get(canvasNodeId.output)!, data: { kind: "output", label: <NodeLabel icon={<LayoutDashboard />} eyebrow={t("Output")} title={t("Dataset Output")} detail={t("Validate · Preview · Publish")} badge={t("Private draft")} /> }, className: "dataset-node output-node", deletable: false },
  ];
  return [...relationNodes, ...joinNodes, ...pipeline];
}

function buildEdges(definition: VisualDefinition, t: ReturnType<typeof useI18n>["t"]): Edge[] {
  const joins = definition.joins ?? [];
  const edges: Edge[] = [];
  joins.forEach((join, index) => {
    const previous = index === 0 ? canvasNodeId.relation(definition.source.relations[0].id) : canvasNodeId.join(joins[index - 1].id);
    edges.push(
      { id: `join-main:${join.id}`, source: previous, target: canvasNodeId.join(join.id), type: "smoothstep", label: join.type === "left" ? "LEFT" : "INNER", animated: false, data: { joinId: join.id }, className: "join-edge", ariaLabel: t("Join edge") },
      { id: `join-table:${join.id}`, source: canvasNodeId.relation(join.right_relation_id), target: canvasNodeId.join(join.id), type: "smoothstep", data: { joinId: join.id }, className: "join-edge", ariaLabel: t("Join edge") },
    );
  });
  const producer = joins.length ? canvasNodeId.join(joins.at(-1)!.id) : canvasNodeId.relation(definition.source.relations[0].id);
  edges.push(
    { id: "pipeline:select", source: producer, target: canvasNodeId.select, type: "smoothstep", deletable: false, className: "pipeline-edge" },
    { id: "pipeline:format", source: canvasNodeId.select, target: canvasNodeId.format, type: "smoothstep", deletable: false, className: "pipeline-edge" },
    { id: "pipeline:output", source: canvasNodeId.format, target: canvasNodeId.output, type: "smoothstep", deletable: false, className: "pipeline-edge" },
  );
  return edges;
}

function NodeLabel({ icon, eyebrow, title, detail, badge }: { icon: React.ReactNode; eyebrow: string; title: string; detail: string; badge: string }) {
  return <div className="canvas-node-label"><header><span>{icon}</span><small>{eyebrow}</small></header><strong>{title}</strong><p>{detail}</p><span className="canvas-node-badge">{badge}</span></div>;
}

function nodeColor(kind: string): string {
  if (kind === "relation") return "#5968ed";
  if (kind === "join") return "#8b5cf6";
  if (kind === "output") return "#15867b";
  return "#64748b";
}
