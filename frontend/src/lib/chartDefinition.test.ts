import { describe, expect, it, vi } from "vitest";

import type { DatasetColumn } from "./api";
import {
  aggregationsFor,
  changeChartType,
  createChartDefinition,
  definitionWithFilters,
} from "./chartDefinition";

const columns: DatasetColumn[] = [
  {
    field_id: "00000000-0000-4000-8000-000000000001",
    name: "ordered_at",
    label: "Ordered at",
    data_type: "timestamp with time zone",
    nullable: false,
    visible: true,
    display_format: null,
    filter_operators: ["eq", "between", "gte", "lte"],
    sortable: true,
  },
  {
    field_id: "00000000-0000-4000-8000-000000000002",
    name: "revenue",
    label: "Revenue",
    data_type: "numeric(12,2)",
    nullable: false,
    visible: true,
    display_format: "number_decimal_2",
    filter_operators: ["eq", "gt", "between"],
    sortable: true,
  },
];

describe("chart studio definition model", () => {
  it("creates a versioned visual definition with a stable measure id", () => {
    vi.spyOn(crypto, "randomUUID").mockReturnValue(
      "00000000-0000-4000-8000-000000000010",
    );
    const definition = createChartDefinition(columns);

    expect(definition).toMatchObject({
      schema_version: 2,
      chart_type: "bar",
      dimensions: [{ field_id: columns[0].field_id }],
      measures: [
        {
          id: "00000000-0000-4000-8000-000000000010",
          field_id: columns[1].field_id,
          aggregation: "sum",
        },
      ],
    });
  });

  it("normalizes KPI shape to one measure and no dimension", () => {
    const definition = createChartDefinition(columns);
    definition.measures.push({
      ...definition.measures[0],
      id: "00000000-0000-4000-8000-000000000011",
    });

    expect(changeChartType(definition, "kpi", columns)).toMatchObject({
      chart_type: "kpi",
      dimensions: [],
      limit: 1,
      measures: [definition.measures[0]],
    });
  });

  it("parses Excel-like persistent filter values through the typed vocabulary", () => {
    const definition = createChartDefinition(columns);
    const result = definitionWithFilters(
      definition,
      [
        {
          id: "filter-1",
          fieldId: columns[1].field_id,
          operator: "between",
          value: "100.50",
          secondValue: "999.99",
        },
      ],
      columns,
    );

    expect(result.filters).toEqual([
      {
        field_id: columns[1].field_id,
        operator: "between",
        values: ["100.50", "999.99"],
      },
    ]);
    expect(aggregationsFor(columns[1])).toContain("avg");
  });
});
