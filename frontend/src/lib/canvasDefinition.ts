import type { CatalogJoinSuggestion, CatalogRelation } from "./api";
import { randomUuid } from "./id";
import {
  relationAlias,
  visualDefinitionFromRelation,
  type VisualDefinition,
} from "./studioDefinition";

export const canvasNodeId = {
  relation: (id: string) => `relation:${id}`,
} as const;

type CanvasNode = NonNullable<
  NonNullable<VisualDefinition["canvas"]>["nodes"]
>[number];
type Relation = VisualDefinition["source"]["relations"][number];
type Join = NonNullable<VisualDefinition["joins"]>[number];
export type JoinCondition = Join["conditions"][number];
export type JoinType = Join["type"];

export const MAX_CANVAS_TABLES = 10;
const COLUMN_GAP = 420;
const ROW_GAP = 300;
const DEFAULT_VIEWPORT = { x: 0, y: 0, zoom: 0.9 };

/** Distance of each table from the main table through its chain of links. */
export function relationDepths(definition: VisualDefinition): Map<string, number> {
  const depths = new Map<string, number>();
  const root = definition.source.relations[0];
  if (!root) return depths;
  depths.set(root.id, 0);
  for (const join of definition.joins ?? []) {
    depths.set(join.right_relation_id, (depths.get(join.left_relation_id) ?? 0) + 1);
  }
  return depths;
}

export function autoLayoutCanvas(definition: VisualDefinition): VisualDefinition {
  const depths = relationDepths(definition);
  const rows = new Map<number, number>();
  const nodes: CanvasNode[] = definition.source.relations.map((relation) => {
    const depth = depths.get(relation.id) ?? 0;
    const row = rows.get(depth) ?? 0;
    rows.set(depth, row + 1);
    return {
      id: canvasNodeId.relation(relation.id),
      x: 40 + depth * COLUMN_GAP,
      y: 40 + row * ROW_GAP,
    };
  });
  return {
    ...definition,
    schema_version: 2,
    canvas: { nodes, viewport: definition.canvas?.viewport ?? DEFAULT_VIEWPORT },
  };
}

export function ensureCanvas(definition: VisualDefinition): VisualDefinition {
  if (!definition.canvas) return autoLayoutCanvas(definition);
  const saved = new Map(
    (definition.canvas.nodes ?? []).map((node) => [node.id, node]),
  );
  const automatic = autoLayoutCanvas(definition).canvas!;
  return {
    ...definition,
    schema_version: 2,
    canvas: {
      viewport: definition.canvas.viewport ?? automatic.viewport!,
      nodes: (automatic.nodes ?? []).map((node) => saved.get(node.id) ?? node),
    },
  };
}

export function updateCanvasNodePositions(
  definition: VisualDefinition,
  positions: { id: string; x: number; y: number }[],
): VisualDefinition {
  const canvas = ensureCanvas(definition).canvas!;
  const next = new Map(positions.map((position) => [position.id, position]));
  return {
    ...definition,
    schema_version: 2,
    canvas: {
      ...canvas,
      nodes: (canvas.nodes ?? []).map((node) => next.get(node.id) ?? node),
    },
  };
}

export function updateCanvasViewport(
  definition: VisualDefinition,
  viewport: { x: number; y: number; zoom: number },
): VisualDefinition {
  const laidOut = ensureCanvas(definition);
  return { ...laidOut, canvas: { ...laidOut.canvas!, viewport } };
}

/** Start a dataset from its main table, with every column selected. */
export function definitionFromMainTable(
  detail: CatalogRelation,
  position: { x: number; y: number },
): VisualDefinition {
  const started = visualDefinitionFromRelation(detail);
  return {
    ...started,
    canvas: {
      nodes: [{ id: canvasNodeId.relation(started.source.relations[0].id), ...position }],
      viewport: DEFAULT_VIEWPORT,
    },
  };
}

/** Link a new table to one already in the dataset. Its columns start unselected. */
export function attachTable(
  definition: VisualDefinition,
  table: { schema: string; name: string },
  relationId: string,
  linkedRelationId: string,
  conditions: JoinCondition[],
  position: { x: number; y: number },
): VisualDefinition {
  const relation: Relation = {
    id: relationId,
    schema: table.schema,
    name: table.name,
    alias: relationAlias(
      table.name,
      definition.source.relations.map((item) => item.alias),
    ),
  };
  const laidOut = ensureCanvas(definition);
  return {
    ...laidOut,
    source: { relations: [...definition.source.relations, relation] },
    joins: [
      ...(definition.joins ?? []),
      {
        id: randomUuid(),
        type: "left",
        left_relation_id: linkedRelationId,
        right_relation_id: relationId,
        conditions,
      },
    ],
    canvas: {
      ...laidOut.canvas!,
      nodes: [
        ...(laidOut.canvas!.nodes ?? []),
        { id: canvasNodeId.relation(relationId), ...position },
      ],
    },
  };
}

/**
 * Orient a foreign-key suggestion so that the new table is the joined side.
 * Returns null unless the suggestion links the candidate to a table already in use.
 */
export function suggestionLink(
  definition: VisualDefinition,
  suggestion: CatalogJoinSuggestion,
  candidateId: string,
): { linkedRelationId: string; conditions: JoinCondition[] } | null {
  const known = new Set(definition.source.relations.map((item) => item.id));
  if (suggestion.source_relation_id === candidateId && known.has(suggestion.target_relation_id))
    return {
      linkedRelationId: suggestion.target_relation_id,
      conditions: suggestion.conditions.map((condition) => ({
        left_column: condition.target_column,
        right_column: condition.source_column,
      })),
    };
  if (suggestion.target_relation_id === candidateId && known.has(suggestion.source_relation_id))
    return {
      linkedRelationId: suggestion.source_relation_id,
      conditions: suggestion.conditions.map((condition) => ({
        left_column: condition.source_column,
        right_column: condition.target_column,
      })),
    };
  return null;
}

export function directJoin(
  definition: VisualDefinition,
  firstRelationId: string,
  secondRelationId: string,
): Join | undefined {
  return (definition.joins ?? []).find(
    (join) =>
      (join.left_relation_id === firstRelationId &&
        join.right_relation_id === secondRelationId) ||
      (join.left_relation_id === secondRelationId &&
        join.right_relation_id === firstRelationId),
  );
}

/** Add one more matching column pair to the link between two tables. */
export function addJoinCondition(
  definition: VisualDefinition,
  joinId: string,
  condition: JoinCondition,
): VisualDefinition {
  return {
    ...definition,
    joins: (definition.joins ?? []).map((join) =>
      join.id !== joinId ||
      join.conditions.length >= 8 ||
      join.conditions.some(
        (item) =>
          item.left_column === condition.left_column &&
          item.right_column === condition.right_column,
      )
        ? join
        : { ...join, conditions: [...join.conditions, condition] },
    ),
  };
}

export function setJoinType(
  definition: VisualDefinition,
  joinId: string,
  type: JoinType,
): VisualDefinition {
  return {
    ...definition,
    joins: (definition.joins ?? []).map((join) =>
      join.id === joinId ? { ...join, type } : join,
    ),
  };
}

/**
 * Unlink a table from the dataset. The table and everything linked through it
 * leave the dataset; their positions are returned so the board can keep showing
 * them as unlinked tables instead of losing the user's work.
 */
export function detachTable(
  definition: VisualDefinition,
  relationId: string,
): {
  definition: VisualDefinition;
  removed: { relation: Relation; position: { x: number; y: number } }[];
} {
  const joins = definition.joins ?? [];
  const removedIds = new Set([relationId]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const join of joins) {
      if (removedIds.has(join.left_relation_id) && !removedIds.has(join.right_relation_id)) {
        removedIds.add(join.right_relation_id);
        changed = true;
      }
    }
  }
  const laidOut = ensureCanvas(definition);
  const positions = new Map(
    (laidOut.canvas!.nodes ?? []).map((node) => [node.id, { x: node.x, y: node.y }]),
  );
  return {
    definition: {
      ...laidOut,
      source: {
        relations: definition.source.relations.filter((item) => !removedIds.has(item.id)),
      },
      fields: definition.fields.filter(
        (field) => !removedIds.has(field.source.relation_id),
      ),
      joins: joins.filter(
        (join) =>
          !removedIds.has(join.left_relation_id) &&
          !removedIds.has(join.right_relation_id),
      ),
      canvas: {
        ...laidOut.canvas!,
        nodes: (laidOut.canvas!.nodes ?? []).filter(
          (node) => !removedIds.has(node.id.slice("relation:".length)),
        ),
      },
    },
    removed: definition.source.relations
      .filter((item) => removedIds.has(item.id))
      .map((relation) => ({
        relation,
        position: positions.get(canvasNodeId.relation(relation.id)) ?? { x: 40, y: 40 },
      })),
  };
}

/** Remove one matching column pair; the last pair removes the whole link. */
export function removeJoinCondition(
  definition: VisualDefinition,
  joinId: string,
  conditionIndex: number,
): ReturnType<typeof detachTable> {
  const join = (definition.joins ?? []).find((item) => item.id === joinId);
  if (!join) return { definition, removed: [] };
  if (join.conditions.length > 1)
    return {
      definition: {
        ...definition,
        joins: (definition.joins ?? []).map((item) =>
          item.id === joinId
            ? {
                ...item,
                conditions: item.conditions.filter((_, index) => index !== conditionIndex),
              }
            : item,
        ),
      },
      removed: [],
    };
  return detachTable(definition, join.right_relation_id);
}

export function catalogTypesCompatible(left: string, right: string): boolean {
  const family = (value: string) => {
    const normalized = value.toLocaleLowerCase();
    if (/(smallint|integer|bigint|numeric|decimal|real|double)/.test(normalized))
      return "number";
    if (/uuid/.test(normalized)) return "uuid";
    if (/(char|text|citext)/.test(normalized)) return "text";
    if (/(timestamp|date|time)/.test(normalized)) return "date";
    if (/bool/.test(normalized)) return "boolean";
    return normalized;
  };
  return family(left) === family(right);
}

/** A short, non-technical name for a PostgreSQL column type. */
export function friendlyType(dataType: string): "number" | "text" | "date" | "yes/no" | "id" | "other" {
  const normalized = dataType.toLocaleLowerCase();
  if (/(smallint|integer|bigint|numeric|decimal|real|double|money)/.test(normalized)) return "number";
  if (/(char|text|citext|name)/.test(normalized)) return "text";
  if (/(timestamp|date|time|interval)/.test(normalized)) return "date";
  if (/bool/.test(normalized)) return "yes/no";
  if (/uuid/.test(normalized)) return "id";
  return "other";
}
