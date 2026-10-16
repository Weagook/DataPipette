import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";

const DATASET_ID = "00000000-0000-4000-8000-000000000001";
const FIELD_ID = "00000000-0000-4000-8000-000000000002";

describe("dataset explorer", () => {
  it("turns semicolon values into a typed in-filter only after Apply", async () => {
    const rowQueries: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const request = new Request(input, init);
        if (request.url.endsWith("/api/v1/auth/me")) {
          return Response.json({ id: "user-1", email: "ada@example.com", display_name: "Ada Analyst", role: "analyst" });
        }
        if (request.url.endsWith(`/api/v1/datasets/${DATASET_ID}/exploration`)) {
          return Response.json({
            dataset_id: DATASET_ID,
            dataset_name: "Customer health",
            can_export: false,
            installation_timezone: "Asia/Almaty",
            snapshot_id: "00000000-0000-4000-8000-000000000003",
            snapshot_row_count: 2,
            snapshot_activated_at: "2026-10-15T10:00:00Z",
            refresh_state: "idle",
            last_refresh_error: null,
            last_refresh_error_at: null,
            is_stale: false,
            columns: [{ field_id: FIELD_ID, name: "region", label: "Region", data_type: "text", nullable: false, visible: true, display_format: null, filter_operators: ["eq", "in", "contains"], sortable: true }],
            default_field_ids: [FIELD_ID],
          });
        }
        if (request.url.endsWith(`/api/v1/datasets/${DATASET_ID}/rows:query`)) {
          rowQueries.push(await request.json());
          return Response.json({
            data: { columns: [{ field_id: FIELD_ID, name: "region", label: "Region", data_type: "text", nullable: false, visible: true, display_format: null }], rows: [{ region: "North" }] },
            meta: { snapshot_id: "00000000-0000-4000-8000-000000000003", snapshot_state: "ready", snapshot_row_count: 2, last_success_at: "2026-10-15T10:00:00Z", refresh_state: "idle", last_refresh_error: null, last_refresh_error_at: null, is_stale: false, next_cursor: null, truncated_by_bytes: false, policy_revision: 1 },
          });
        }
        throw new Error(`Unexpected request: ${request.url}`);
      }),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[`/datasets/${DATASET_ID}/table`]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(
      await screen.findByRole("heading", { name: "Customer health" }, { timeout: 5_000 }),
    ).toBeVisible();
    await waitFor(() => expect(rowQueries).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Add filter" }));
    fireEvent.change(screen.getByLabelText("Filter condition"), { target: { value: "in" } });
    fireEvent.change(screen.getByLabelText("Filter value"), { target: { value: "North; South" } });
    expect(rowQueries).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));

    await waitFor(() => expect(rowQueries).toHaveLength(2));
    expect(rowQueries[1]).toMatchObject({
      filters: [{ field_id: FIELD_ID, operator: "in", values: ["North", "South"] }],
    });
  });
});
