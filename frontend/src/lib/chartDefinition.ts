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

export function createChartDefinition(
  columns: DatasetColumn[],
  chartType: ChartType = "bar",
): ChartDefinition {
  const dimension = columns.find(isDimensionColumn);
  const measure =
    columns.find((column) => isNumeric(column.data_type)) ??
    columns.find((column) => aggregationsFor(column).length > 0);
  if (!measure)
    throw new Error("The published dataset has no chartable fields.");
  if (chartType !== "kpi" && !dimension) {
    throw new Error("The published dataset has no valid dimension field.");
  }
  return {
    schema_version: 2,
    chart_type: chartType,
    dimensions:
      chartType === "kpi" || !dimension
        ? []
        : [{ field_id: dimension.field_id, time_bucket: null }],
    measures: [newMeasure(measure)],
    filters: [],
    sorts: [],
    limit: chartType === "kpi" ? 1 : 100,
  };
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
  return {
    ...definition,
    chart_type: chartType,
    dimensions,
    measures,
    sorts: [],
    limit: chartType === "kpi" ? 1 : Math.max(definition.limit, 10),
  };
}

export function newMeasure(
  column: DatasetColumn,
): ChartDefinition["measures"][number] {
  const aggregation: ChartAggregation = isNumeric(column.data_type)
    ? "sum"
    : "count";
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
