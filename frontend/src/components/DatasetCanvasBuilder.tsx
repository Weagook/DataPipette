import {
  Background,
  BackgroundVariant,
  ConnectionMode,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  applyNodeChanges,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeChange,
  type NodeProps,
  type Viewport,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Crown,
  EyeOff,
  Link2,
  LoaderCircle,
  Plus,
  Redo2,
  Search,
  Table2,
  Trash2,
  Undo2,
  WandSparkles,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getCatalogRelation,
  listCatalogRelations,
  listCatalogSchemas,
  suggestCatalogJoins,
  type CatalogRelation,
} from "../lib/api";
import {
  MAX_CANVAS_TABLES,
  addJoinCondition,
  attachTable,
  autoLayoutCanvas,
  canvasNodeId,
  catalogTypesCompatible,
  definitionFromMainTable,
  detachTable,
  directJoin,
  ensureCanvas,
  friendlyType,
  removeJoinCondition,
  setJoinType,
  suggestionLink,
  updateCanvasNodePositions,
  updateCanvasViewport,
  type JoinType,
} from "../lib/canvasDefinition";
import { useI18n } from "../lib/i18n";
import { randomUuid } from "../lib/id";
import { queryKeys } from "../lib/query";
import {
  appendVisualField,
  type DisplayFormat,
  type VisualDefinition,
} from "../lib/studioDefinition";

type Translate = ReturnType<typeof useI18n>["t"];
type PendingTable = { key: string; schema: string; name: string; x: number; y: number };
type Notice = { tone: "info" | "error"; text: string };
type SelectedLink = { joinId: string; conditionIndex: number };
type TableNodeData = {
  connectionId: string;
  schema: string;
  name: string;
  isMain: boolean;
  isPending: boolean;
  selectedColumns: string[];
  linkedColumns: string[];
  onToggleColumn: (column: string, selected: boolean) => void;
  onSelectAll: (columns: string[], selected: boolean) => void;
  onRemove: () => void;
};
type TableNode = Node<TableNodeData, "table">;

const TABLE_MIME = "application/x-datapipette-table";
const COLLAPSED_COLUMNS = 12;
const FORMATS: { value: "" | DisplayFormat; label: string }[] = [
  { value: "", label: "As in the source" },
  { value: "date_dd_mm_yyyy", label: "Date 31-12-2026" },
  { value: "date_yyyy_mm_dd", label: "Date 2026-12-31" },
  { value: "datetime_dd_mm_yyyy_hh_mm", label: "Date and time" },
  { value: "number_integer", label: "Whole number" },
  { value: "number_decimal_2", label: "Number with 2 decimals" },
  { value: "percent", label: "Percent" },
];
const nodeTypes = { table: TableCard };

export function DatasetCanvasBuilder(props: {
  connectionId: string;
  definition: VisualDefinition | null;
  onChange: (definition: VisualDefinition | null) => void;
  allowEmpty?: boolean;
}) {
  return (
    <ReactFlowProvider>
      <CanvasBoard {...props} />
    </ReactFlowProvider>
  );
}

function CanvasBoard({
  connectionId,
  definition,
  onChange,
  allowEmpty = false,
}: {
  connectionId: string;
  definition: VisualDefinition | null;
  onChange: (definition: VisualDefinition | null) => void;
  allowEmpty?: boolean;
}) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const flow = useReactFlow();
  const past = useRef<(VisualDefinition | null)[]>([]);
  const future = useRef<(VisualDefinition | null)[]>([]);
  const [, setHistoryRevision] = useState(0);
  const [pending, setPending] = useState<PendingTable[]>([]);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [selectedLink, setSelectedLink] = useState<SelectedLink | null>(null);
  const [adding, setAdding] = useState(false);
  const stable = useMemo(() => (definition ? ensureCanvas(definition) : null), [definition]);

  const commit = useCallback(
    (next: VisualDefinition | null, record = true) => {
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
    if (!past.current.length) return;
    future.current.push(definition);
    onChange(past.current.pop()!);
    setSelectedLink(null);
    setHistoryRevision((value) => value + 1);
  }, [definition, onChange]);
  const redo = useCallback(() => {
    if (!future.current.length) return;
    past.current.push(definition);
    onChange(future.current.pop()!);
    setSelectedLink(null);
    setHistoryRevision((value) => value + 1);
  }, [definition, onChange]);

  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "z") return;
      const target = event.target as HTMLElement | null;
      if (target?.matches("input, textarea, select, [contenteditable='true']")) return;
      event.preventDefault();
      if (event.shiftKey) redo();
      else undo();
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [redo, undo]);

  const freePosition = useCallback(() => {
    const occupied = [
      ...(stable?.canvas?.nodes ?? []),
      ...pending.map((item) => ({ x: item.x, y: item.y })),
    ];
    if (!occupied.length) return { x: 40, y: 40 };
    return { x: Math.max(...occupied.map((item) => item.x)) + 420, y: 40 };
  }, [pending, stable]);

  const loadTable = useCallback(
    (schema: string, name: string) =>
      queryClient.fetchQuery({
        queryKey: queryKeys.catalogRelation(connectionId, schema, name),
        queryFn: () => getCatalogRelation(connectionId, schema, name),
      }),
    [connectionId, queryClient],
  );

  const addTable = useCallback(
    async (schema: string, name: string, position = freePosition()) => {
      setNotice(null);
      if ((stable?.source.relations.length ?? 0) + pending.length >= MAX_CANVAS_TABLES) {
        setNotice({ tone: "error", text: t("A dataset can combine at most {count} tables.", { count: MAX_CANVAS_TABLES }) });
        return;
      }
      setAdding(true);
      try {
        const detail = await loadTable(schema, name);
        if (!stable) {
          commit(definitionFromMainTable(detail, position));
          setNotice({ tone: "info", text: t("“{table}” is the main table. Untick columns you do not need, or drag related tables next to it.", { table: name }) });
          return;
        }
        const candidateId = randomUuid();
        const suggestions = await suggestCatalogJoins(connectionId, [
          ...stable.source.relations.map((item) => ({ id: item.id, schema: item.schema, name: item.name })),
          { id: candidateId, schema, name },
        ]).catch(() => []);
        const link = suggestions
          .map((suggestion) => suggestionLink(stable, suggestion, candidateId))
          .find((item) => item !== null);
        if (link) {
          commit(attachTable(stable, { schema, name }, candidateId, link.linkedRelationId, link.conditions, position));
          const linked = stable.source.relations.find((item) => item.id === link.linkedRelationId);
          setNotice({
            tone: "info",
            text: t("Linked automatically: {left} → {right}. Click the line to change how rows are matched.", {
              left: `${linked?.name}.${link.conditions[0].left_column}`,
              right: `${name}.${link.conditions[0].right_column}`,
            }),
          });
        } else {
          setPending((current) => [...current, { key: candidateId, schema, name, ...position }]);
          setNotice({ tone: "info", text: t("Now link “{table}”: drag from one of its columns to the matching column of another table.", { table: name }) });
        }
      } catch {
        setNotice({ tone: "error", text: t("The table could not be loaded. Check the connection and try again.") });
      } finally {
        setAdding(false);
      }
    },
    [commit, connectionId, freePosition, loadTable, pending.length, stable, t],
  );

  const keepAsPending = useCallback((removed: ReturnType<typeof detachTable>["removed"]) => {
    if (!removed.length) return;
    setPending((current) => [
      ...current,
      ...removed.map(({ relation, position }) => ({
        key: relation.id,
        schema: relation.schema,
        name: relation.name,
        ...position,
      })),
    ]);
    setNotice({ tone: "info", text: t("Unlinked tables stay on the board with a dashed border until you link them again.") });
  }, [t]);

  const removeTable = useCallback(
    (nodeId: string) => {
      setSelectedLink(null);
      if (nodeId.startsWith("pending:")) {
        setPending((current) => current.filter((item) => `pending:${item.key}` !== nodeId));
        return;
      }
      if (!stable) return;
      const relationId = nodeId.slice("relation:".length);
      if (relationId === stable.source.relations[0]?.id) {
        if (stable.source.relations.length > 1) {
          setNotice({ tone: "error", text: t("Remove the linked tables first: the main table holds the dataset together.") });
        } else if (allowEmpty) {
          commit(null);
        } else {
          setNotice({ tone: "error", text: t("A dataset needs at least one table.") });
        }
        return;
      }
      const { definition: next, removed } = detachTable(stable, relationId);
      commit(next);
      keepAsPending(removed.filter((item) => item.relation.id !== relationId));
    },
    [allowEmpty, commit, keepAsPending, stable, t],
  );

  const toggleColumn = useCallback(
    (relationId: string, column: string, selected: boolean) => {
      if (!stable) return;
      commit(
        selected
          ? appendVisualField(stable, relationId, column)
          : {
              ...stable,
              fields: stable.fields.filter(
                (field) => !(field.source.relation_id === relationId && field.source.column === column),
              ),
            },
      );
    },
    [commit, stable],
  );
  const selectAll = useCallback(
    (relationId: string, columns: string[], selected: boolean) => {
      if (!stable) return;
      commit(
        selected
          ? columns.reduce((current, column) => appendVisualField(current, relationId, column), stable)
          : { ...stable, fields: stable.fields.filter((field) => field.source.relation_id !== relationId) },
      );
    },
    [commit, stable],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      setNotice(null);
      const sourceColumn = handleColumn(connection.sourceHandle);
      const targetColumn = handleColumn(connection.targetHandle);
      if (!sourceColumn || !targetColumn || connection.source === connection.target) return;
      const ends = [
        { node: connection.source, column: sourceColumn },
        { node: connection.target, column: targetColumn },
      ];
      const tableOf = (nodeId: string) =>
        nodeId.startsWith("pending:")
          ? pending.find((item) => `pending:${item.key}` === nodeId)
          : stable?.source.relations.find((item) => canvasNodeId.relation(item.id) === nodeId);
      const types = ends.map((end) => {
        const table = tableOf(end.node);
        const detail = table
          ? queryClient.getQueryData<CatalogRelation>(queryKeys.catalogRelation(connectionId, table.schema, table.name))
          : undefined;
        return detail?.columns.find((column) => column.name === end.column)?.data_type;
      });
      if (types[0] && types[1] && !catalogTypesCompatible(types[0], types[1])) {
        setNotice({ tone: "error", text: t("These columns hold different kinds of values ({left} and {right}) and cannot be matched.", { left: t(friendlyType(types[0])), right: t(friendlyType(types[1])) }) });
        return;
      }
      const pendingEnds = ends.filter((end) => end.node.startsWith("pending:"));
      if (!stable || pendingEnds.length === 2) {
        setNotice({ tone: "error", text: t("Link one of these tables to the dataset first, then link the other one to it.") });
        return;
      }
      if (pendingEnds.length === 1) {
        const loose = pendingEnds[0];
        const anchored = ends.find((end) => end !== loose)!;
        const table = pending.find((item) => `pending:${item.key}` === loose.node)!;
        commit(
          attachTable(
            stable,
            table,
            table.key,
            anchored.node.slice("relation:".length),
            [{ left_column: anchored.column, right_column: loose.column }],
            { x: table.x, y: table.y },
          ),
        );
        setPending((current) => current.filter((item) => item.key !== table.key));
        setNotice({ tone: "info", text: t("“{table}” is linked. Tick the columns you want to keep from it.", { table: table.name }) });
        return;
      }
      const first = ends[0].node.slice("relation:".length);
      const second = ends[1].node.slice("relation:".length);
      const join = directJoin(stable, first, second);
      if (!join) {
        setNotice({ tone: "error", text: t("These tables are already connected through other tables. Each table can be linked only once.") });
        return;
      }
      const leftEnd = join.left_relation_id === first ? ends[0] : ends[1];
      const rightEnd = leftEnd === ends[0] ? ends[1] : ends[0];
      commit(addJoinCondition(stable, join.id, { left_column: leftEnd.column, right_column: rightEnd.column }));
      setNotice({ tone: "info", text: t("Added another matching column to this link. Rows must match on all linked columns.") });
    },
    [commit, connectionId, pending, queryClient, stable, t],
  );

  const removeLink = useCallback(
    (link: SelectedLink) => {
      if (!stable) return;
      const { definition: next, removed } = removeJoinCondition(stable, link.joinId, link.conditionIndex);
      commit(next);
      keepAsPending(removed);
      setSelectedLink(null);
    },
    [commit, keepAsPending, stable],
  );

  const derivedNodes = useMemo(
    () => buildNodes(connectionId, stable, pending, { toggleColumn, selectAll, removeTable }),
    [connectionId, pending, removeTable, selectAll, stable, toggleColumn],
  );
  const [nodes, setNodes] = useState<TableNode[]>(derivedNodes);
  useEffect(() => setNodes(derivedNodes), [derivedNodes]);
  const edges = useMemo(() => buildEdges(stable, selectedLink, t), [selectedLink, stable, t]);

  const onNodesChange = useCallback(
    (changes: NodeChange<TableNode>[]) =>
      setNodes((current) => applyNodeChanges(changes.filter((change) => change.type !== "remove"), current)),
    [],
  );
  const onNodeDragStop = useCallback(
    (_event: unknown, moved: TableNode) => {
      if (moved.id.startsWith("pending:")) {
        setPending((current) =>
          current.map((item) => (`pending:${item.key}` === moved.id ? { ...item, ...moved.position } : item)),
        );
      } else if (stable) {
        commit(updateCanvasNodePositions(stable, [{ id: moved.id, ...moved.position }]));
      }
    },
    [commit, stable],
  );
  const onMoveEnd = useCallback(
    (event: MouseEvent | TouchEvent | null, viewport: Viewport) => {
      if (!stable || (!definition?.canvas && event === null)) return;
      const current = stable.canvas?.viewport ?? { x: 0, y: 0, zoom: 1 };
      if (
        Math.abs(current.x - viewport.x) < 0.5 &&
        Math.abs(current.y - viewport.y) < 0.5 &&
        Math.abs(current.zoom - viewport.zoom) < 0.001
      )
        return;
      commit(updateCanvasViewport(stable, viewport), false);
    },
    [commit, definition?.canvas, stable],
  );

  const linkedJoin = selectedLink
    ? stable?.joins?.find((join) => join.id === selectedLink.joinId)
    : undefined;
  const isEmpty = !stable && pending.length === 0;

  return (
    <div className="table-canvas">
      <header className="table-canvas-toolbar">
        <div>
          <strong>{t("Dataset builder")}</strong>
          <small>{t("Drag tables from the list onto the board, then draw a line between matching columns to link them.")}</small>
        </div>
        <nav aria-label={t("Builder actions")}>
          <button className="icon-button" type="button" disabled={!past.current.length} onClick={undo} aria-label={t("Undo")} title={`${t("Undo")} · Ctrl/⌘ Z`}>
            <Undo2 />
          </button>
          <button className="icon-button" type="button" disabled={!future.current.length} onClick={redo} aria-label={t("Redo")} title={`${t("Redo")} · Ctrl/⌘ Shift Z`}>
            <Redo2 />
          </button>
          <button className="button secondary small" type="button" disabled={!stable} onClick={() => stable && commit(autoLayoutCanvas({ ...stable, canvas: undefined }))}>
            <WandSparkles /> {t("Tidy up")}
          </button>
        </nav>
      </header>
      <div className="table-canvas-body">
        <TableLibrary
          connectionId={connectionId}
          usedTables={[...(stable?.source.relations ?? []), ...pending].map((item) => `${item.schema}.${item.name}`)}
          busy={adding}
          onAdd={(schema, name) => void addTable(schema, name)}
        />
        <div
          className="table-canvas-board"
          aria-label={t("Dataset board")}
          onDragOver={(event) => {
            if (event.dataTransfer.types.includes(TABLE_MIME)) {
              event.preventDefault();
              event.dataTransfer.dropEffect = "copy";
            }
          }}
          onDrop={(event) => {
            const raw = event.dataTransfer.getData(TABLE_MIME);
            if (!raw) return;
            event.preventDefault();
            const table = JSON.parse(raw) as { schema: string; name: string };
            void addTable(table.schema, table.name, flow.screenToFlowPosition({ x: event.clientX, y: event.clientY }));
          }}
        >
          <ReactFlow<TableNode>
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onNodeDragStop={onNodeDragStop}
            onMoveEnd={onMoveEnd}
            onConnect={onConnect}
            onEdgeClick={(_event, edge) => {
              const data = edge.data as SelectedLink | undefined;
              if (data) setSelectedLink(data);
            }}
            onEdgesDelete={(deleted) => {
              const data = deleted[0]?.data as SelectedLink | undefined;
              if (data) removeLink(data);
            }}
            onPaneClick={() => setSelectedLink(null)}
            connectionMode={ConnectionMode.Loose}
            // The mouse wheel keeps scrolling the page; zoom uses the controls or a pinch.
            zoomOnScroll={false}
            preventScrolling={false}
            defaultViewport={stable?.canvas?.viewport ?? { x: 0, y: 0, zoom: 0.9 }}
            fitView={Boolean(definition && !definition.canvas)}
            fitViewOptions={{ padding: 0.2 }}
            minZoom={0.2}
            maxZoom={1.6}
            deleteKeyCode={["Backspace", "Delete"]}
            nodesFocusable
            edgesFocusable
          >
            <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} />
            <MiniMap pannable zoomable maskColor="rgba(245, 245, 242, .72)" />
            <Controls showInteractive={false} />
          </ReactFlow>
          {isEmpty && (
            <div className="table-canvas-empty">
              <Table2 />
              <strong>{t("Drag the first table here")}</strong>
              <p>{t("It becomes the main table: every row of the dataset comes from it.")}</p>
            </div>
          )}
          {selectedLink && linkedJoin && stable && (
            <LinkPanel
              definition={stable}
              joinId={linkedJoin.id}
              conditionIndex={selectedLink.conditionIndex}
              onType={(type) => commit(setJoinType(stable, linkedJoin.id, type))}
              onRemove={() => removeLink(selectedLink)}
              onClose={() => setSelectedLink(null)}
            />
          )}
          {notice && (
            <div className={`table-canvas-notice ${notice.tone}`} role={notice.tone === "error" ? "alert" : "status"}>
              <span>{notice.text}</span>
              <button className="icon-button" type="button" aria-label={t("Dismiss")} onClick={() => setNotice(null)}>
                <X />
              </button>
            </div>
          )}
        </div>
      </div>
      {stable && <ResultColumns definition={stable} onChange={commit} />}
    </div>
  );
}

function TableLibrary({
  connectionId,
  usedTables,
  busy,
  onAdd,
}: {
  connectionId: string;
  usedTables: string[];
  busy: boolean;
  onAdd: (schema: string, name: string) => void;
}) {
  const { t } = useI18n();
  const [schema, setSchema] = useState("");
  const [search, setSearch] = useState("");
  const schemas = useQuery({
    queryKey: queryKeys.catalogSchemas(connectionId),
    queryFn: () => listCatalogSchemas(connectionId),
  });
  const relations = useQuery({
    queryKey: queryKeys.catalogRelations(connectionId, schema),
    queryFn: () => listCatalogRelations(connectionId, schema),
    enabled: Boolean(schema),
  });
  useEffect(() => {
    const available = schemas.data?.data ?? [];
    if (!schema && available.length)
      setSchema((available.find((item) => item.name === "public") ?? available[0]).name);
  }, [schema, schemas.data]);
  const needle = search.trim().toLocaleLowerCase();
  const visible = (relations.data?.data ?? []).filter((item) =>
    item.name.toLocaleLowerCase().includes(needle),
  );
  return (
    <aside className="table-library" aria-label={t("Tables")}>
      <header>
        <strong>{t("Tables")}</strong>
        {busy && <LoaderCircle className="spin" aria-label={t("Adding table")} />}
      </header>
      {(schemas.data?.data.length ?? 0) > 1 && (
        <label>
          <span>{t("Table group")}</span>
          <select value={schema} onChange={(event) => setSchema(event.target.value)}>
            {schemas.data?.data.map((item) => (
              <option key={item.name} value={item.name}>{item.name}</option>
            ))}
          </select>
        </label>
      )}
      <label className="table-library-search">
        <Search />
        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t("Find a table")} aria-label={t("Find a table")} />
      </label>
      {(schemas.isError || relations.isError) && (
        <p className="table-library-error">{t("The list of tables could not be loaded.")}</p>
      )}
      <ul>
        {visible.map((item) => {
          const used = usedTables.includes(`${schema}.${item.name}`);
          return (
            <li
              key={item.name}
              draggable
              onDragStart={(event) => {
                event.dataTransfer.setData(TABLE_MIME, JSON.stringify({ schema, name: item.name }));
                event.dataTransfer.effectAllowed = "copy";
              }}
              className={used ? "used" : ""}
              title={t("Drag onto the board")}
            >
              <Table2 />
              <span>
                <strong>{item.name}</strong>
                {used && <small>{t("on the board")}</small>}
              </span>
              <button className="icon-button" type="button" disabled={busy} aria-label={t("Add {table} to the board", { table: item.name })} onClick={() => onAdd(schema, item.name)}>
                <Plus />
              </button>
            </li>
          );
        })}
        {relations.isSuccess && !visible.length && <li className="table-library-none">{t("No tables found")}</li>}
      </ul>
    </aside>
  );
}

function TableCard({ data }: NodeProps<TableNode>) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const detail = useQuery({
    queryKey: queryKeys.catalogRelation(data.connectionId, data.schema, data.name),
    queryFn: () => getCatalogRelation(data.connectionId, data.schema, data.name),
  });
  const columns = detail.data?.columns ?? [];
  const selected = new Set(data.selectedColumns);
  const linked = new Set(data.linkedColumns);
  const visible =
    expanded || columns.length <= COLLAPSED_COLUMNS
      ? columns
      : columns.filter(
          (column, index) => index < COLLAPSED_COLUMNS - 2 || selected.has(column.name) || linked.has(column.name),
        );
  const allSelected = columns.length > 0 && columns.every((column) => selected.has(column.name));
  return (
    <div className={`table-card${data.isPending ? " pending" : ""}${data.isMain ? " main" : ""}`}>
      <header>
        <span className="table-card-icon">{data.isMain ? <Crown /> : data.isPending ? <Link2 /> : <Table2 />}</span>
        <div>
          <strong title={`${data.schema}.${data.name}`}>{data.name}</strong>
          <small>
            {data.isMain
              ? t("Main table")
              : data.isPending
                ? t("Not linked yet")
                : t("{selected} of {total} columns", { selected: selected.size, total: columns.length })}
          </small>
        </div>
        <button className="icon-button nodrag" type="button" aria-label={t("Remove {table} from the board", { table: data.name })} onClick={data.onRemove}>
          <Trash2 />
        </button>
      </header>
      {data.isPending && <p className="table-card-hint">{t("Drag from a column here to the matching column of another table.")}</p>}
      {!data.isPending && columns.length > 0 && (
        <button className="table-card-all nodrag" type="button" onClick={() => data.onSelectAll(columns.map((column) => column.name), !allSelected)}>
          {allSelected ? t("Clear all") : t("Select all")}
        </button>
      )}
      {detail.isPending && <p className="table-card-hint">{t("Loading columns…")}</p>}
      {detail.isError && <p className="table-card-hint error">{t("Columns could not be loaded.")}</p>}
      <ul>
        {visible.map((column) => (
          <li key={column.name} className={linked.has(column.name) ? "linked" : ""}>
            <Handle type="source" position={Position.Left} id={`in:${encodeURIComponent(column.name)}`} className="column-handle" />
            <label className="nodrag">
              <input
                type="checkbox"
                aria-label={column.name}
                checked={selected.has(column.name)}
                disabled={data.isPending}
                onChange={(event) => data.onToggleColumn(column.name, event.target.checked)}
              />
              <span>{column.name}</span>
            </label>
            <small className={`type-${friendlyType(column.data_type).replace("/", "")}`}>{t(friendlyType(column.data_type))}</small>
            <Handle type="source" position={Position.Right} id={`out:${encodeURIComponent(column.name)}`} className="column-handle" />
          </li>
        ))}
      </ul>
      {!expanded && visible.length < columns.length && (
        <button className="table-card-more nodrag" type="button" onClick={() => setExpanded(true)}>
          {t("Show all {count} columns", { count: columns.length })}
        </button>
      )}
    </div>
  );
}

function LinkPanel({
  definition,
  joinId,
  conditionIndex,
  onType,
  onRemove,
  onClose,
}: {
  definition: VisualDefinition;
  joinId: string;
  conditionIndex: number;
  onType: (type: JoinType) => void;
  onRemove: () => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const join = definition.joins!.find((item) => item.id === joinId)!;
  const left = definition.source.relations.find((item) => item.id === join.left_relation_id)!;
  const right = definition.source.relations.find((item) => item.id === join.right_relation_id)!;
  const condition = join.conditions[conditionIndex] ?? join.conditions[0];
  return (
    <section className="link-panel nodrag" aria-label={t("Link settings")}>
      <header>
        <Link2 />
        <strong>{left.name} ↔ {right.name}</strong>
        <button className="icon-button" type="button" aria-label={t("Close")} onClick={onClose}>
          <X />
        </button>
      </header>
      <p className="link-panel-columns">
        {t("Rows match when {left} equals {right}.", { left: `${left.name}.${condition.left_column}`, right: `${right.name}.${condition.right_column}` })}
      </p>
      <fieldset>
        <legend>{t("Which rows to keep?")}</legend>
        <label>
          <input type="radio" name={`link-${join.id}`} checked={join.type === "left"} onChange={() => onType("left")} />
          <span>
            <strong>{t("All rows from “{table}”", { table: left.name })}</strong>
            <small>{t("Rows without a match in “{other}” are kept with empty values.", { other: right.name })}</small>
          </span>
        </label>
        <label>
          <input type="radio" name={`link-${join.id}`} checked={join.type === "inner"} onChange={() => onType("inner")} />
          <span>
            <strong>{t("Only rows that match")}</strong>
            <small>{t("Rows without a pair in both tables are left out.")}</small>
          </span>
        </label>
      </fieldset>
      <button className="button danger small" type="button" onClick={onRemove}>
        <Trash2 /> {join.conditions.length > 1 ? t("Remove this matching column") : t("Unlink “{table}”", { table: right.name })}
      </button>
    </section>
  );
}

function ResultColumns({
  definition,
  onChange,
}: {
  definition: VisualDefinition;
  onChange: (definition: VisualDefinition) => void;
}) {
  const { t } = useI18n();
  const [editing, setEditing] = useState<string | null>(null);
  const tableName = new Map(definition.source.relations.map((item) => [item.id, item.name]));
  const field = definition.fields.find((item) => item.id === editing);
  const update = (id: string, change: (item: VisualDefinition["fields"][number]) => VisualDefinition["fields"][number]) =>
    onChange({ ...definition, fields: definition.fields.map((item) => (item.id === id ? change(item) : item)) });
  return (
    <section className="result-columns" aria-label={t("Columns in the result")}>
      <header>
        <strong>{t("Columns in the result")}</strong>
        <small>{definition.fields.length ? t("Click a column to rename it or change how values look.") : t("Tick columns on the tables above.")}</small>
      </header>
      <div className="result-column-chips">
        {definition.fields.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`${editing === item.id ? "active" : ""}${item.display.visible ? "" : " hidden-column"}`}
            onClick={() => setEditing(editing === item.id ? null : item.id)}
            title={`${tableName.get(item.source.relation_id)}.${item.source.column}`}
          >
            {!item.display.visible && <EyeOff />}
            {item.display.label}
          </button>
        ))}
      </div>
      {field && (
        <div className="result-column-editor">
          <small>{tableName.get(field.source.relation_id)}.{field.source.column}</small>
          <label>
            <span>{t("Name in the table")}</span>
            <input value={field.display.label} onChange={(event) => update(field.id, (item) => ({ ...item, display: { ...item.display, label: event.target.value } }))} />
          </label>
          <label>
            <span>{t("How values look")}</span>
            <select value={field.display.format ?? ""} onChange={(event) => update(field.id, (item) => ({ ...item, display: { ...item.display, format: (event.target.value || null) as DisplayFormat | null } }))}>
              {FORMATS.map((format) => (
                <option key={format.value} value={format.value}>{t(format.label)}</option>
              ))}
            </select>
          </label>
          <label className="result-column-visible">
            <input type="checkbox" checked={!field.display.visible} onChange={(event) => update(field.id, (item) => ({ ...item, display: { ...item.display, visible: !event.target.checked } }))} />
            <span>{t("Hide in the table (keep for charts)")}</span>
          </label>
          <button className="button ghost small" type="button" onClick={() => { setEditing(null); onChange({ ...definition, fields: definition.fields.filter((item) => item.id !== field.id) }); }}>
            <Trash2 /> {t("Remove from result")}
          </button>
        </div>
      )}
    </section>
  );
}

function buildNodes(
  connectionId: string,
  definition: VisualDefinition | null,
  pending: PendingTable[],
  actions: {
    toggleColumn: (relationId: string, column: string, selected: boolean) => void;
    selectAll: (relationId: string, columns: string[], selected: boolean) => void;
    removeTable: (nodeId: string) => void;
  },
): TableNode[] {
  const positions = new Map((definition?.canvas?.nodes ?? []).map((node) => [node.id, { x: node.x, y: node.y }]));
  const linked = new Map<string, string[]>();
  for (const join of definition?.joins ?? []) {
    for (const condition of join.conditions) {
      linked.set(join.left_relation_id, [...(linked.get(join.left_relation_id) ?? []), condition.left_column]);
      linked.set(join.right_relation_id, [...(linked.get(join.right_relation_id) ?? []), condition.right_column]);
    }
  }
  const tables: TableNode[] = (definition?.source.relations ?? []).map((relation, index) => {
    const id = canvasNodeId.relation(relation.id);
    return {
      id,
      type: "table",
      position: positions.get(id) ?? { x: 40, y: 40 },
      deletable: false,
      data: {
        connectionId,
        schema: relation.schema,
        name: relation.name,
        isMain: index === 0,
        isPending: false,
        selectedColumns: definition!.fields
          .filter((field) => field.source.relation_id === relation.id)
          .map((field) => field.source.column),
        linkedColumns: linked.get(relation.id) ?? [],
        onToggleColumn: (column, selected) => actions.toggleColumn(relation.id, column, selected),
        onSelectAll: (columns, selected) => actions.selectAll(relation.id, columns, selected),
        onRemove: () => actions.removeTable(id),
      },
    };
  });
  const loose: TableNode[] = pending.map((table) => ({
    id: `pending:${table.key}`,
    type: "table",
    position: { x: table.x, y: table.y },
    deletable: false,
    data: {
      connectionId,
      schema: table.schema,
      name: table.name,
      isMain: false,
      isPending: true,
      selectedColumns: [],
      linkedColumns: [],
      onToggleColumn: () => undefined,
      onSelectAll: () => undefined,
      onRemove: () => actions.removeTable(`pending:${table.key}`),
    },
  }));
  return [...tables, ...loose];
}

function buildEdges(
  definition: VisualDefinition | null,
  selected: SelectedLink | null,
  t: Translate,
): Edge[] {
  const names = new Map((definition?.source.relations ?? []).map((item) => [item.id, item.name]));
  return (definition?.joins ?? []).flatMap((join) =>
    join.conditions.map((condition, conditionIndex) => ({
      id: `link:${join.id}:${conditionIndex}`,
      source: canvasNodeId.relation(join.left_relation_id),
      sourceHandle: `out:${encodeURIComponent(condition.left_column)}`,
      target: canvasNodeId.relation(join.right_relation_id),
      targetHandle: `in:${encodeURIComponent(condition.right_column)}`,
      label:
        conditionIndex === 0
          ? join.type === "left"
            ? t("all rows from “{table}”", { table: names.get(join.left_relation_id) ?? "" })
            : t("only matches")
          : undefined,
      data: { joinId: join.id, conditionIndex },
      selected: selected?.joinId === join.id && selected.conditionIndex === conditionIndex,
      className: `table-link ${join.type}`,
      ariaLabel: t("Link between {left} and {right}", {
        left: names.get(join.left_relation_id) ?? "",
        right: names.get(join.right_relation_id) ?? "",
      }),
    })),
  );
}

function handleColumn(handle: string | null | undefined): string | null {
  if (!handle) return null;
  const separator = handle.indexOf(":");
  return separator < 0 ? null : decodeURIComponent(handle.slice(separator + 1));
}
