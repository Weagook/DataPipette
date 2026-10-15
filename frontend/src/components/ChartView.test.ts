import { describe, expect, it } from "vitest";

import type { DashboardItemResult } from "../lib/api";
import { buildChartOption } from "./ChartView";

function result(chartType: DashboardItemResult["meta"]["chart_type"]): DashboardItemResult {
  return {
    data: {
      columns: [
        {
          key: "region",
          role: "dimension",
          field_id: "11111111-1111-4111-8111-111111111111",
          measure_id: null,
          label: "Region",
          data_type: "text",
          time_bucket: null,
          timezone: null,
        },
        {
          key: "revenue",
          role: "measure",
          field_id: "22222222-2222-4222-8222-222222222222",
          measure_id: "33333333-3333-4333-8333-333333333333",
          label: "Revenue",
          data_type: "numeric",
          time_bucket: null,
          timezone: null,
        },
      ],
      rows: [
        { region: "North", revenue: "120.50" },
        { region: "South", revenue: "80" },
      ],
    },
    meta: {
      chart_id: "44444444-4444-4444-8444-444444444444",
      chart_name: "Revenue by region",
      chart_type: chartType,
      chart_revision: 1,
      dashboard_id: "55555555-5555-4555-8555-555555555555",
      dashboard_revision: 1,
      dataset_id: "66666666-6666-4666-8666-666666666666",
      item_id: "77777777-7777-4777-8777-777777777777",
      is_stale: false,
      last_refresh_error: null,
      last_refresh_error_at: null,
      policy_revision: 0,
      refresh_state: "idle",
      snapshot_activated_at: "2026-10-15T10:00:00Z",
      snapshot_id: "88888888-8888-4888-8888-888888888888",
      truncated_by_bytes: false,
    },
  };
}

describe("chart options", () => {
  it("maps saved bar metadata to categorical series without guessing", () => {
    const option = buildChartOption(result("bar"));
    expect(option.xAxis).toMatchObject({ type: "category", data: ["North", "South"] });
    expect(option.series).toEqual([
      expect.objectContaining({ type: "bar", name: "Revenue", data: [120.5, 80] }),
    ]);
  });

  it("maps donut metadata to a ring series", () => {
    const option = buildChartOption(result("donut"));
    expect(option.series).toEqual([
      expect.objectContaining({
        type: "pie",
        radius: ["48%", "72%"],
        data: [
          { name: "North", value: 120.5 },
          { name: "South", value: 80 },
        ],
      }),
    ]);
  });
});
