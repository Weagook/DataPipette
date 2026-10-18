import type { CatalogJoinSuggestion, CatalogRelation } from "./api";
import { randomUuid } from "./id";
import {
  appendJoinedRelation,
  type VisualDefinition,
} from "./studioDefinition";

export const canvasNodeId = {
  relation: (id: string) => `relation:${id}`,
  join: (id: string) => `join:${id}`,
  select: "select",
  format: "format",
  output: "output",
} as const;

type CanvasNode = NonNullable<
  NonNullable<VisualDefinition["canvas"]>["nodes"]
>[number];

export function canvasNodeIds(definition: VisualDefinition): string[] {
  return [
    ...definition.source.relations.map((relation) =>
      canvasNodeId.relation(relation.id),
    ),
    ...(definition.joins ?? []).map((join) => canvasNodeId.join(join.id)),
    canvasNodeId.select,
    canvasNodeId.format,
    canvasNodeId.output,
  ];
}

export function autoLayoutCanvas(
  definition: VisualDefinition,
): VisualDefinition {
  const joins = definition.joins ?? [];
  const positions: CanvasNode[] = [];
  definition.source.relations.forEach((relation, index) => {
    positions.push({
      id: canvasNodeId.relation(relation.id),
      x: 60,
      y: 90 + index * 220,
    });
  });
  joins.forEach((join, index) => {
    positions.push({
      id: canvasNodeId.join(join.id),
      x: 390 + index * 260,
      y: 200,
    });
  });
  const pipelineX = 420 + joins.length * 260;
  positions.push(
    { id: canvasNodeId.select, x: pipelineX, y: 90 },
    { id: canvasNodeId.format, x: pipelineX + 280, y: 90 },
    { id: canvasNodeId.output, x: pipelineX + 560, y: 90 },
  );
  return {
    ...definition,
    schema_version: 2,
    canvas: {
      nodes: positions,
      viewport: { x: 0, y: 0, zoom: 0.85 },
    },
  };
}

export function ensureCanvas(definition: VisualDefinition): VisualDefinition {
  if (!definition.canvas) return autoLayoutCanvas(definition);
  const expected = new Set(canvasNodeIds(definition));
  const saved = new Map(
    (definition.canvas.nodes ?? [])
      .filter((node) => expected.has(node.id))
      .map((node) => [node.id, node]),
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
  return {
    ...ensureCanvas(definition),
    canvas: { ...ensureCanvas(definition).canvas!, viewport },
  };
}

export function appendManualJoinedRelation(
  definition: VisualDefinition,
  detail: CatalogRelation,
  leftRelationId: string,
  leftColumn: string,
  rightColumn: string,
): VisualDefinition {
  const candidateId = randomUuid();
  const suggestion: CatalogJoinSuggestion = {
    foreign_key_name: "manual",
    source_relation_id: candidateId,
    target_relation_id: leftRelationId,
    conditions: [
      { source_column: rightColumn, target_column: leftColumn },
    ],
  };
  return appendRelationFromSuggestion(
    definition,
    detail,
    suggestion,
    candidateId,
  );
}

export function appendRelationFromSuggestion(
  definition: VisualDefinition,
  detail: CatalogRelation,
  suggestion: CatalogJoinSuggestion,
  candidateId = randomUuid(),
): VisualDefinition {
  return ensureCanvas(
    appendJoinedRelation(definition, detail, suggestion, candidateId),
  );
}

export function removeJoinSubtree(
  definition: VisualDefinition,
  joinId: string,
): VisualDefinition {
  const joins = definition.joins ?? [];
  const target = joins.find((join) => join.id === joinId);
  if (!target) return definition;
  const removed = new Set([target.right_relation_id]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const join of joins) {
      if (removed.has(join.left_relation_id) && !removed.has(join.right_relation_id)) {
        removed.add(join.right_relation_id);
        changed = true;
      }
    }
  }
  const next: VisualDefinition = {
    ...definition,
    source: {
      relations: definition.source.relations.filter(
        (relation) => !removed.has(relation.id),
      ),
    },
    fields: definition.fields.filter(
      (field) => !removed.has(field.source.relation_id),
    ),
    joins: joins.filter(
      (join) =>
        !removed.has(join.left_relation_id) &&
        !removed.has(join.right_relation_id),
    ),
  };
  return ensureCanvas(next);
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
