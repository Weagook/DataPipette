import type {
  ApiSchemas,
  CatalogJoinSuggestion,
  CatalogRelation,
  DatasetDefinition,
} from "./api";

export type VisualDefinition = ApiSchemas["VisualDefinitionPayload"];
export type SqlDefinition = ApiSchemas["SqlDefinitionPayload"];
export type DisplayFormat = ApiSchemas["FieldDisplayFormat"];

export function relationAlias(name: string, used: string[] = []): string {
  const base =
    name
      .toLocaleLowerCase()
      .replace(/[^a-z0-9_]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 80) || "source";
  let alias = base;
  let suffix = 2;
  while (used.includes(alias)) alias = `${base.slice(0, 75)}_${suffix++}`;
  return alias;
}

export function visualDefinitionFromRelation(
  detail: CatalogRelation,
): VisualDefinition {
  const relationId = crypto.randomUUID();
  return {
    kind: "visual",
    schema_version: 2,
    source: {
      relations: [
        {
          id: relationId,
          schema: detail.schema_name,
          name: detail.name,
          alias: relationAlias(detail.name),
        },
      ],
    },
    fields: detail.columns.map((column) => ({
      id: crypto.randomUUID(),
      source: { relation_id: relationId, column: column.name },
      name: column.name,
      display: { label: humanize(column.name), format: null, visible: true },
    })),
    joins: [],
  };
}

export function appendJoinedRelation(
  definition: VisualDefinition,
  detail: CatalogRelation,
  suggestion: CatalogJoinSuggestion,
  candidateId: string,
): VisualDefinition {
  const aliases = definition.source.relations.map((relation) => relation.alias);
  const relation = {
    id: candidateId,
    schema: detail.schema_name,
    name: detail.name,
    alias: relationAlias(detail.name, aliases),
  };
  const sourceIsCandidate = suggestion.source_relation_id === candidateId;
  const leftRelationId = sourceIsCandidate
    ? suggestion.target_relation_id
    : suggestion.source_relation_id;
  const rightRelationId = sourceIsCandidate
    ? suggestion.source_relation_id
    : suggestion.target_relation_id;
  return {
    ...definition,
    source: { relations: [...definition.source.relations, relation] },
    fields: [
      ...definition.fields,
      ...detail.columns.map((column) => ({
        id: crypto.randomUUID(),
        source: { relation_id: candidateId, column: column.name },
        name: uniqueFieldName(
          column.name,
          definition.fields.map((field) => field.name),
        ),
        display: { label: humanize(column.name), format: null, visible: true },
      })),
    ],
    joins: [
      ...(definition.joins ?? []),
      {
        id: crypto.randomUUID(),
        type: "left",
        left_relation_id: leftRelationId,
        right_relation_id: rightRelationId,
        conditions: suggestion.conditions.map((condition) => ({
          left_column: sourceIsCandidate
            ? condition.target_column
            : condition.source_column,
          right_column: sourceIsCandidate
            ? condition.source_column
            : condition.target_column,
        })),
      },
    ],
  };
}

export function emptySqlDefinition(): SqlDefinition {
  return {
    kind: "sql",
    schema_version: 1,
    statement: "SELECT\n  id\nFROM public.example",
    fields: [newSqlField("id")],
  };
}

export function newSqlField(name = "field"): SqlDefinition["fields"][number] {
  return {
    id: crypto.randomUUID(),
    name,
    display: { label: humanize(name), format: null, visible: true },
  };
}

export function definitionIsReady(
  definition: DatasetDefinition | null,
): boolean {
  if (!definition || definition.fields.length === 0) return false;
  if (definition.kind === "sql") return definition.statement.trim().length > 0;
  return definition.source.relations.length > 0 && definition.fields.length > 0;
}

function uniqueFieldName(name: string, used: string[]): string {
  if (!used.includes(name)) return name;
  let suffix = 2;
  while (used.includes(`${name}_${suffix}`)) suffix += 1;
  return `${name}_${suffix}`;
}

function humanize(value: string): string {
  const spaced = value.replace(/[_-]+/g, " ").trim();
  return spaced
    ? spaced.charAt(0).toLocaleUpperCase() + spaced.slice(1)
    : value;
}
