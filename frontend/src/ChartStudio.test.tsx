import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";

const DATASET_ID = "00000000-0000-4000-8000-000000000011";
const CHART_ID = "00000000-0000-4000-8000-000000000012";
const DATE_ID = "00000000-0000-4000-8000-000000000013";
const REVENUE_ID = "00000000-0000-4000-8000-000000000014";

describe("chart studio", () => {
  it("creates a saved aggregate chart through the public API", async () => {
    document.cookie = "datapipette_csrf=test-token; path=/";
    let createBody: unknown;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const request = new Request(input, init);
        if (request.url.endsWith("/api/v1/auth/me"))
          return Response.json({
            id: "user-1",
            email: "ada@example.com",
            display_name: "Ada Analyst",
            role: "analyst",
          });
        if (request.url.includes("/api/v1/datasets?"))
          return Response.json({
            data: [
              {
                id: DATASET_ID,
                name: "Orders",
                owner_id: "user-1",
                connection_id: "connection-1",
                active_version_id: "version-1",
                active_snapshot_id: "snapshot-1",
                draft_revision: 1,
                created_at: "2026-10-16T00:00:00Z",
                updated_at: "2026-10-16T00:00:00Z",
              },
            ],
            meta: { next_cursor: null },
          });
        if (request.url.endsWith(`/api/v1/datasets/${DATASET_ID}/exploration`))
          return Response.json({
            dataset_id: DATASET_ID,
            dataset_name: "Orders",
            snapshot_id: "snapshot-1",
            snapshot_activated_at: "2026-10-16T00:00:00Z",
            snapshot_row_count: 12000,
            installation_timezone: "Asia/Almaty",
            refresh_state: "idle",
            is_stale: false,
            last_refresh_error: null,
            last_refresh_error_at: null,
            can_export: true,
            default_field_ids: [DATE_ID, REVENUE_ID],
            columns: [
              {
                field_id: DATE_ID,
                name: "ordered_at",
                label: "Ordered at",
                data_type: "timestamp with time zone",
                nullable: false,
                visible: true,
                display_format: null,
                filter_operators: ["eq", "between"],
                sortable: true,
              },
              {
                field_id: REVENUE_ID,
                name: "revenue",
                label: "Revenue",
                data_type: "numeric(12,2)",
                nullable: false,
                visible: true,
                display_format: "number_decimal_2",
                filter_operators: ["eq", "gt", "between"],
                sortable: true,
              },
            ],
          });
        if (
          request.url.endsWith("/api/v1/charts") &&
          request.method === "POST"
        ) {
          createBody = await request.json();
          return Response.json(
            {
              id: CHART_ID,
              dataset_id: DATASET_ID,
              owner_id: "user-1",
              name: "Revenue trend",
              revision: 1,
              definition: (createBody as { definition: unknown }).definition,
              created_at: "2026-10-16T00:00:00Z",
              updated_at: "2026-10-16T00:00:00Z",
            },
            { status: 201, headers: { ETag: '"chart-1"' } },
          );
        }
        if (request.url.endsWith(`/api/v1/charts/${CHART_ID}`))
          return Response.json(
            {
              id: CHART_ID,
              dataset_id: DATASET_ID,
              owner_id: "user-1",
              name: "Revenue trend",
              revision: 1,
              definition: (createBody as { definition: unknown }).definition,
              created_at: "2026-10-16T00:00:00Z",
              updated_at: "2026-10-16T00:00:00Z",
            },
            { headers: { ETag: '"chart-1"' } },
          );
        if (request.url.endsWith(`/api/v1/charts/${CHART_ID}/data:query`))
          return Response.json({
            data: { columns: [], rows: [] },
            meta: {
              chart_id: CHART_ID,
              chart_name: "Revenue trend",
              chart_revision: 1,
              chart_type: "bar",
              dataset_id: DATASET_ID,
              snapshot_id: "snapshot-1",
              snapshot_activated_at: "2026-10-16T00:00:00Z",
              refresh_state: "idle",
              is_stale: false,
              last_refresh_error: null,
              last_refresh_error_at: null,
              policy_revision: 1,
              truncated_by_bytes: false,
            },
          });
        throw new Error(`Unexpected request: ${request.method} ${request.url}`);
      }),
    );
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/charts/new"]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(
      await screen.findByRole(
        "heading",
        { name: "Build a visual answer" },
        { timeout: 5_000 },
      ),
    ).toBeVisible();
    fireEvent.change(screen.getByLabelText("Chart name"), {
      target: { value: "Revenue trend" },
    });
    fireEvent.change(await screen.findByLabelText("Published dataset"), {
      target: { value: DATASET_ID },
    });
    await screen.findByRole("button", { name: /Line/ });
    fireEvent.click(screen.getByRole("button", { name: /Line/ }));
    fireEvent.change(screen.getByLabelText("Time bucket"), {
      target: { value: "month" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save and preview" }));

    await waitFor(() =>
      expect(createBody).toMatchObject({
        name: "Revenue trend",
        dataset_id: DATASET_ID,
        definition: {
          schema_version: 2,
          chart_type: "line",
          dimensions: [{ field_id: DATE_ID, time_bucket: "month" }],
          measures: [{ field_id: REVENUE_ID, aggregation: "sum" }],
        },
      }),
    );
  });
});
