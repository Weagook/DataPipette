import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";

const DATASET_ID = "00000000-0000-4000-8000-000000000201";

describe("overview workspace", () => {
  it("guides the next step and lists datasets whose load failed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(new Request(input, init).url).pathname;
        const page = (data: unknown[]) => Response.json({ data, meta: { next_cursor: null } });
        if (path === "/api/v1/auth/me")
          return Response.json({ id: "user-1", email: "ada@example.com", display_name: "Ada", role: "analyst" });
        if (path === "/api/v1/connections") return page([{ id: "connection-1" }]);
        if (path === "/api/v1/dashboards") return page([]);
        if (path === "/api/v1/charts") return page([]);
        if (path === "/api/v1/datasets")
          return page([
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
          ]);
        if (path === `/api/v1/datasets/${DATASET_ID}/freshness`)
          return Response.json({
            dataset_id: DATASET_ID,
            data_state: "stale",
            refresh_state: "idle",
            is_stale: true,
            snapshot_id: "snapshot-1",
            snapshot_row_count: 10,
            last_success_at: "2026-10-16T00:00:00Z",
            last_refresh_error: "source_unavailable",
            last_refresh_error_at: "2026-10-17T00:00:00Z",
            active_refresh_run_id: null,
            refresh_requested_at: null,
            refresh_started_at: null,
          });
        throw new Error(`Unexpected request: ${path}`);
      }),
    );
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/overview"]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { name: "Getting started" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Start" })).toHaveAttribute("href", "/charts/new");
    expect(await screen.findByRole("heading", { name: "Needs attention" })).toBeVisible();
    expect(screen.getAllByRole("link", { name: "Orders" })[0]).toHaveAttribute("href", `/datasets/${DATASET_ID}/draft`);
    expect(screen.getByText(/Last load failed/)).toBeVisible();
  });
});
