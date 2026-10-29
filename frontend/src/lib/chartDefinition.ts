import type { ApiSchemas, ChartDefinition, DatasetColumn } from "./api";
import {
  buildSnapshotFilters,
  type FilterDraft,
  type FilterOperator,
} from "./datasetQuery";
import { randomUuid } from "./id";

export type ChartType = ApiSchemas["ChartType"];
export type ChartAggregation = ApiSchemas["ChartAggregation"];
export type ChartTimeBucket = ApiSchemas["ChartTimeBucket"];
export type ChartSort = ApiSchemas["ChartSortPayload"];

export const chartTypes: { value: ChartType; label: string; hint: string }[] = [
  { value: "bar", label: "Bar", hint: "Compare categories" },
  { value: "line", label: "Line", hint: "Show change over time" },
  { value: "pie", label: "Pie", hint: "Show composition" },
  { value: "donut", label: "Donut", hint: "Compact composition" },
  { value: "kpi", label: "KPI", hint: "One headline number" },
];

export const aggregationLabels: Record<ChartAggregation, string> = {
  count: "Count",
  count_distinct: "Count distinct",
  sum: "Sum",
  avg: "Average",
  min: "Minimum",
  max: "Maximum",
};

/** Pick defaults a non-specialist would expect: a category, counted rows, biggest first. */
export function createChartDefinition(
  columns: DatasetColumn[],
  chartType: ChartType = "bar",
): ChartDefinition {
  const counted = rowCountColumn(columns);
  if (!counted) throw new Error("The published dataset has no chartable fields.");
  const dimension = defaultDimension(columns);
  if (chartType !== "kpi" && !dimension) {
    throw new Error("The published dataset has no valid dimension field.");
  }
  const temporal = dimension ? isTemporalColumn(dimension) : false;
  const measure = newMeasure(counted, "count");
  return {
    schema_version: 2,
    chart_type: chartType,
    dimensions:
      chartType === "kpi" || !dimension
        ? []
        : [{ field_id: dimension.field_id, time_bucket: temporal ? "month" : null }],
    measures: [measure],
    filters: [],
    sorts:
      chartType === "kpi" || !dimension
        ? []
        : temporal
          ? [{ target_type: "dimension", target_id: dimension.field_id, direction: "asc" }]
          : [{ target_type: "measure", target_id: measure.id, direction: "desc" }],
    limit: chartType === "kpi" ? 1 : temporal ? 100 : 20,
  };
}

/** A column whose non-null count equals the number of rows, when one exists. */
export function rowCountColumn(columns: DatasetColumn[]): DatasetColumn | undefined {
  return (
    columns.find((column) => !column.nullable && aggregationsFor(column).length > 0) ??
    columns.find((column) => aggregationsFor(column).length > 0)
  );
}

export function isRowCount(
  measure: ChartDefinition["measures"][number],
  columns: DatasetColumn[],
): boolean {
  return measure.aggregation === "count" && measure.field_id === rowCountColumn(columns)?.field_id;
}

function defaultDimension(columns: DatasetColumn[]): DatasetColumn | undefined {
  const candidates = columns.filter(isDimensionColumn);
  const identifier = (column: DatasetColumn) =>
    /(^id$|_id$|^uuid$)/i.test(column.name) || /uuid/i.test(column.data_type);
  return (
    candidates.find((column) => typeFamily(column.data_type) === "text" && !identifier(column)) ??
    candidates.find(isTemporalColumn) ??
    candidates.find((column) => !identifier(column)) ??
    candidates[0]
  );
}

export function changeChartType(
  definition: ChartDefinition,
  chartType: ChartType,
  columns: DatasetColumn[],
): ChartDefinition {
  const existingDimension = definition.dimensions?.[0];
  const fallbackDimension = columns.find(isDimensionColumn)?.field_id;
  const dimensions =
    chartType === "kpi"
      ? []
      : existingDimension
        ? [existingDimension]
        : fallbackDimension
          ? [{ field_id: fallbackDimension, time_bucket: null }]
          : [];
  const measures = ["pie", "donut", "kpi"].includes(chartType)
    ? definition.measures.slice(0, 1)
    : definition.measures;
  return withNaturalOrder(
    { ...definition, chart_type: chartType, dimensions, measures, sorts: [], limit: chartType === "kpi" ? 1 : Math.max(definition.limit, 10) },
    columns,
  );
}

/** Dates read left to right in time; categories read biggest first. */
export function withNaturalOrder(definition: ChartDefinition, columns: DatasetColumn[]): ChartDefinition {
  const dimension = definition.dimensions?.[0];
  const column = columns.find((item) => item.field_id === dimension?.field_id);
  if (definition.chart_type === "kpi" || !dimension || !column) return { ...definition, sorts: [] };
  if (isTemporalColumn(column))
    return {
      ...definition,
      sorts: [{ target_type: "dimension", target_id: dimension.field_id, direction: "asc" }],
      limit: Math.max(definition.limit, 100),
    };
  return {
    ...definition,
    sorts: [{ target_type: "measure", target_id: definition.measures[0].id, direction: "desc" }],
  };
}

export function newMeasure(
  column: DatasetColumn,
  aggregation: ChartAggregation = isNumeric(column.data_type) ? "sum" : "count",
): ChartDefinition["measures"][number] {
  return {
    id: randomUuid(),
    field_id: column.field_id,
    aggregation,
    label: `${aggregationLabels[aggregation]} of ${column.label}`,
  };
}

export function aggregationsFor(column: DatasetColumn): ChartAggregation[] {
  const family = typeFamily(column.data_type);
  if (family === "binary") return [];
  if (family === "number" || family === "integer") {
    return ["count", "count_distinct", "sum", "avg", "min", "max"];
  }
  if (["date", "datetime", "time", "text"].includes(family)) {
    return ["count", "count_distinct", "min", "max"];
  }
  return ["count", "count_distinct"];
}

export function isDimensionColumn(column: DatasetColumn): boolean {
  return !["binary", "json"].includes(typeFamily(column.data_type));
}

export function isTemporalColumn(column: DatasetColumn): boolean {
  return ["date", "datetime"].includes(typeFamily(column.data_type));
}

export function isTimestampColumn(column: DatasetColumn): boolean {
  return typeFamily(column.data_type) === "datetime";
}

export function filterDraftsFromDefinition(
  definition: ChartDefinition,
): FilterDraft[] {
  return (definition.filters ?? []).map((filter) => ({
    id: randomUuid(),
    fieldId: filter.field_id,
    operator: filter.operator,
    value:
      filter.operator === "in"
        ? (filter.values ?? []).map(displayFilterValue).join("; ")
        : filter.operator === "between"
          ? displayFilterValue(filter.values?.[0])
          : displayFilterValue(filter.value),
    secondValue:
      filter.operator === "between"
        ? displayFilterValue(filter.values?.[1])
        : "",
  }));
}

export function definitionWithFilters(
  definition: ChartDefinition,
  filters: FilterDraft[],
  columns: DatasetColumn[],
): ChartDefinition {
  return { ...definition, filters: buildSnapshotFilters(filters, columns) };
}

export function defaultOperator(column: DatasetColumn): FilterOperator {
  return column.filter_operators[0] ?? "eq";
}

export function chartDefinitionIsReady(definition: ChartDefinition): boolean {
  const dimensions = definition.dimensions ?? [];
  if (definition.chart_type === "kpi" && dimensions.length !== 0) return false;
  if (definition.chart_type !== "kpi" && dimensions.length !== 1) return false;
  if (definition.measures.length < 1 || definition.measures.length > 5)
    return false;
  if (
    ["pie", "donut", "kpi"].includes(definition.chart_type) &&
    definition.measures.length !== 1
  )
    return false;
  return definition.limit >= 1 && definition.limit <= 1000;
}

function displayFilterValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function isNumeric(dataType: string): boolean {
  return ["integer", "number"].includes(typeFamily(dataType));
}

function typeFamily(dataType: string): string {
  const value = dataType.toLocaleLowerCase().replace(/\s+/g, " ").trim();
  if (["smallint", "integer", "bigint"].includes(value)) return "integer";
  if (
    ["real", "double precision"].includes(value) ||
    /^(numeric|decimal)\(\d+,-?\d+\)$/.test(value)
  )
    return "number";
  if (value === "date") return "date";
  if (/^timestamp(?:\(\d+\))?(?: with(?:out)? time zone)?$/.test(value))
    return "datetime";
  if (/^time(?:\(\d+\))? without time zone$/.test(value)) return "time";
  if (["json", "jsonb"].includes(value)) return "json";
  if (value === "bytea") return "binary";
  return "text";
}
