import { describe, expect, it } from "vitest";

import { filterKindFor, readSelections, selectionsForItem, writeSelections, type DashboardFilter } from "./dashboardFilters";

const filters: DashboardFilter[] = [
  { id: "region", label: "Region", kind: "values", targets: [{ item_id: "a", field_id: "f1" }] },
  { id: "ordered", label: "Ordered", kind: "date_range", targets: [{ item_id: "b", field_id: "f2" }] },
];

describe("dashboard filter helpers", () => {
  it("offers only column types the backend can filter", () => {
    expect(filterKindFor("text")).toBe("values");
    expect(filterKindFor("character varying(20)")).toBe("values");
    expect(filterKindFor("bigint")).toBe("values");
    expect(filterKindFor("boolean")).toBe("values");
    expect(filterKindFor("date")).toBe("date_range");
    expect(filterKindFor("timestamp with time zone")).toBe("date_range");
    expect(filterKindFor("numeric(12,2)")).toBeNull();
    expect(filterKindFor("jsonb")).toBeNull();
  });

  it("round-trips selections through the address and ignores unknown or empty ones", () => {
    const params = writeSelections(new URLSearchParams("tab=1"), [
      { filter_id: "region", values: ["East"] },
      { filter_id: "ordered", from: "2026-01-01", to: null },
      { filter_id: "region-empty", values: [] },
    ]);
    expect(params.get("tab")).toBe("1");
    expect(readSelections(params, filters)).toEqual([
      { filter_id: "region", values: ["East"] },
      { filter_id: "ordered", from: "2026-01-01", to: null },
    ]);
    expect(readSelections(new URLSearchParams("filters=not-json"), filters)).toEqual([]);
    expect(readSelections(new URLSearchParams(`filters=${encodeURIComponent('[{"filter_id":"gone","values":["x"]},{"filter_id":"ordered","from":"01/02/2026"}]')}`), filters)).toEqual([]);
    expect(writeSelections(params, []).has("filters")).toBe(false);
  });

  it("sends each chart only the choices of filters that target it", () => {
    const selections = [
      { filter_id: "region", values: ["East"] },
      { filter_id: "ordered", from: "2026-01-01" },
    ];
    expect(selectionsForItem("a", filters, selections)).toEqual([selections[0]]);
    expect(selectionsForItem("b", filters, selections)).toEqual([selections[1]]);
    expect(selectionsForItem("c", filters, selections)).toEqual([]);
  });
});
