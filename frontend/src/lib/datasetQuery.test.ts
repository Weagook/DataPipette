import { describe, expect, it } from "vitest";

import type { DatasetColumn } from "./api";
import { buildSnapshotFilters, formatDatasetValue, type FilterDraft } from "./datasetQuery";

const columns: DatasetColumn[] = [
  {
    field_id: "11111111-1111-4111-8111-111111111111",
    name: "region",
    label: "Region",
    data_type: "text",
    nullable: false,
    visible: true,
    display_format: null,
    filter_operators: ["eq", "in", "contains"],
    sortable: true,
  },
  {
    field_id: "22222222-2222-4222-8222-222222222222",
    name: "amount",
    label: "Amount",
    data_type: "numeric(12,2)",
    nullable: false,
    visible: true,
    display_format: "number_decimal_2",
    filter_operators: ["gt", "between"],
    sortable: true,
  },
];

function draft(values: Partial<FilterDraft>): FilterDraft {
  return {
    id: "filter-1",
    fieldId: columns[0].field_id,
    operator: "eq",
    value: "",
    secondValue: "",
    ...values,
  };
}

describe("dataset query model", () => {
  it("turns Excel-like semicolon input into an explicit typed in-list", () => {
    expect(buildSnapshotFilters([draft({ operator: "in", value: "North; South ; Enterprise" })], columns)).toEqual([
      { field_id: columns[0].field_id, operator: "in", values: ["North", "South", "Enterprise"] },
    ]);
  });

  it("keeps bounded decimals exact while building a range", () => {
    expect(buildSnapshotFilters([
      draft({ fieldId: columns[1].field_id, operator: "between", value: "10.20", secondValue: "99.95" }),
    ], columns)).toEqual([
      { field_id: columns[1].field_id, operator: "between", values: ["10.20", "99.95"] },
    ]);
  });

  it("rejects incomplete filters before a request is sent", () => {
    expect(() => buildSnapshotFilters([draft({ operator: "contains" })], columns)).toThrow("Region needs a value");
  });

  it("applies published display formatting", () => {
    expect(formatDatasetValue("1200.5", columns[1])).toBe("1,200.50");
    expect(formatDatasetValue("9007199254740993.25", columns[1])).toBe("9,007,199,254,740,993.25");
  });

  it("uses the installation timezone only for timezone-aware timestamps", () => {
    const base = {
      field_id: "33333333-3333-4333-8333-333333333333",
      name: "occurred_at",
      label: "Occurred at",
      nullable: false,
      visible: true,
      display_format: "datetime_dd_mm_yyyy_hh_mm" as const,
      filter_operators: ["eq" as const],
      sortable: true,
    };
    expect(formatDatasetValue("2026-01-01T00:30:00Z", { ...base, data_type: "timestamp with time zone" }, "Asia/Almaty")).toBe("01-01-2026 05:30");
    expect(formatDatasetValue("2026-01-01T00:30:00", { ...base, data_type: "timestamp without time zone" }, "Asia/Almaty")).toBe("01-01-2026 00:30");
  });
});
