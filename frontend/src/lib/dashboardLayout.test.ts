import { describe, expect, it } from "vitest";

import {
  addChart,
  compactLayout,
  emptyDashboardDefinition,
  firstAvailablePosition,
  isValidLayout,
  removeItem,
  updateItem,
} from "./dashboardLayout";

describe("dashboard layout", () => {
  it("places charts into the first free area of the 12-column grid", () => {
    const first = addChart(emptyDashboardDefinition(), "chart-1", "item-1");
    const second = addChart(first, "chart-2", "item-2");
    expect(first.items?.[0]).toMatchObject({ x: 0, y: 0, width: 6, height: 4 });
    expect(second.items?.[1]).toMatchObject({ x: 6, y: 0, width: 6, height: 4 });
    expect(isValidLayout(second.items ?? [])).toBe(true);
  });

  it("rejects moves that overlap or leave the grid", () => {
    const definition = {
      schema_version: 1 as const,
      items: [
        { id: "item-1", chart_id: "chart-1", x: 0, y: 0, width: 6, height: 4 },
        { id: "item-2", chart_id: "chart-2", x: 6, y: 0, width: 6, height: 4 },
      ],
    };
    expect(updateItem(definition, "item-2", { x: 5 })).toBeNull();
    expect(updateItem(definition, "item-2", { x: 7 })).toBeNull();
    expect(updateItem(definition, "item-2", { y: 10_000 })).toBeNull();
    expect(updateItem(definition, "item-2", { height: 101 })).toBeNull();
    expect(updateItem(definition, "item-2", { y: 4 })).not.toBeNull();
  });

  it("compacts gaps without changing chart identities or geometry size", () => {
    const definition = {
      schema_version: 1 as const,
      items: [
        { id: "item-1", chart_id: "chart-1", x: 0, y: 8, width: 6, height: 4 },
        { id: "item-2", chart_id: "chart-2", x: 6, y: 12, width: 6, height: 4 },
      ],
    };
    expect(compactLayout(definition).items).toEqual([
      { id: "item-1", chart_id: "chart-1", x: 0, y: 0, width: 6, height: 4 },
      { id: "item-2", chart_id: "chart-2", x: 6, y: 0, width: 6, height: 4 },
    ]);
    expect(firstAvailablePosition(definition.items)).toMatchObject({ y: 0 });
  });

  it("keeps filters through layout edits and drops targets of removed items", () => {
    const definition = {
      schema_version: 2 as const,
      items: [
        { id: "item-1", chart_id: "chart-1", x: 0, y: 0, width: 6, height: 4 },
        { id: "item-2", chart_id: "chart-2", x: 6, y: 0, width: 6, height: 4 },
      ],
      filters: [
        { id: "both", label: "Region", kind: "values" as const, targets: [{ item_id: "item-1", field_id: "f1" }, { item_id: "item-2", field_id: "f2" }] },
        { id: "only-two", label: "Ordered", kind: "date_range" as const, targets: [{ item_id: "item-2", field_id: "f3" }] },
      ],
    };
    expect(compactLayout(definition).filters).toEqual(definition.filters);
    expect(updateItem(definition, "item-1", { height: 5 })?.filters).toEqual(definition.filters);
    expect(removeItem(definition, "item-2").filters).toEqual([
      { id: "both", label: "Region", kind: "values", targets: [{ item_id: "item-1", field_id: "f1" }] },
    ]);
  });
});
