import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";

const USER_ID = "00000000-0000-4000-8000-000000000001";
const CHART_ID = "00000000-0000-4000-8000-000000000002";
const DATASET_ID = "00000000-0000-4000-8000-000000000003";
const DASHBOARD_ID = "00000000-0000-4000-8000-000000000004";
const ITEM_ID = "00000000-0000-4000-8000-000000000005";
const FILTER_ID = "00000000-0000-4000-8000-000000000006";
const REGION_FIELD = "00000000-0000-4000-8000-000000000007";

const item = { id: ITEM_ID, chart_id: CHART_ID, x: 0, y: 0, width: 6, height: 4 };
const regionFilter = {
  id: FILTER_ID,
  label: "Region",
  kind: "values",
  targets: [{ item_id: ITEM_ID, field_id: REGION_FIELD }],
};

function dashboard(definition: unknown) {
  return {
    id: DASHBOARD_ID,
    owner_id: USER_ID,
    name: "Sales",
    revision: 1,
    definition,
    created_at: "2026-10-16T00:00:00Z",
    updated_at: "2026-10-16T00:00:00Z",
  };
}

function renderAt(path: string) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("dashboard filters", () => {
  it("lets a viewer pick values that reach only the mapped chart query", async () => {
    const itemBodies: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const request = new Request(input, init);
        const path = new URL(request.url).pathname;
        if (path === "/api/v1/auth/me")
          return Response.json({ id: "viewer-1", email: "v@example.com", display_name: "Viewer", role: "viewer" });
        if (path === `/api/v1/dashboards/${DASHBOARD_ID}`)
          return Response.json(dashboard({ schema_version: 2, items: [item], filters: [regionFilter] }));
        if (path === `/api/v1/dashboards/${DASHBOARD_ID}/filters/${FILTER_ID}/values`)
          return Response.json({ dashboard_id: DASHBOARD_ID, filter_id: FILTER_ID, values: ["East", "West"], truncated: false });
        if (path === `/api/v1/dashboards/${DASHBOARD_ID}/items/${ITEM_ID}/data:query`) {
          const text = await request.text();
          itemBodies.push(text ? JSON.parse(text) : null);
          return Response.json({ detail: "busy" }, { status: 503 });
        }
        throw new Error(`Unexpected request: ${request.method} ${request.url}`);
      }),
    );
    renderAt(`/dashboards/${DASHBOARD_ID}`);

    const chip = await screen.findByText("Region:");
    await waitFor(() => expect(itemBodies).toEqual([null]));
    fireEvent.click(chip);
    const panel = await screen.findByRole("group", { name: "Region" });
    fireEvent.click(await within(panel).findByLabelText("East"));

    await waitFor(() => expect(itemBodies).toContainEqual({ filters: [{ filter_id: FILTER_ID, values: ["East"] }] }));
    fireEvent.click(screen.getByRole("button", { name: /Clear filters/ }));
    expect(screen.queryByRole("button", { name: /Clear filters/ })).not.toBeInTheDocument();
  });

  it("adds a filter from a column of the dashboard charts and saves it", async () => {
    document.cookie = "datapipette_csrf=test-token; path=/";
    let saved: Record<string, unknown> | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const request = new Request(input, init);
        const path = new URL(request.url).pathname;
        if (path === "/api/v1/auth/me")
          return Response.json({ id: USER_ID, email: "ada@example.com", display_name: "Ada", role: "analyst" });
        if (path === "/api/v1/charts")
          return Response.json({
            data: [
              {
                id: CHART_ID,
                dataset_id: DATASET_ID,
                owner_id: USER_ID,
                name: "Revenue by region",
                revision: 1,
                definition: { schema_version: 2, chart_type: "kpi", dimensions: [], measures: [], filters: [], sorts: [], limit: 100 },
                created_at: "2026-10-16T00:00:00Z",
                updated_at: "2026-10-16T00:00:00Z",
              },
            ],
            meta: { next_cursor: null },
          });
        if (path === `/api/v1/datasets/${DATASET_ID}/exploration`)
          return Response.json({
            dataset_id: DATASET_ID,
            columns: [
              { field_id: REGION_FIELD, name: "region", label: "Region", data_type: "text", visible: true },
              { field_id: "amount", name: "amount", label: "Amount", data_type: "numeric(12,2)", visible: true },
            ],
          });
        if (path === `/api/v1/dashboards/${DASHBOARD_ID}` && request.method === "GET")
          return Response.json(dashboard({ schema_version: 1, items: [item] }), { headers: { ETag: '"dashboard-1"' } });
        if (path === `/api/v1/dashboards/${DASHBOARD_ID}` && request.method === "PUT") {
          saved = (await request.json()) as Record<string, unknown>;
          return Response.json({ ...dashboard(saved.definition), revision: 2 }, { headers: { ETag: '"dashboard-2"' } });
        }
        if (path === `/api/v1/dashboards/${DASHBOARD_ID}/grants`) return Response.json({ data: [] });
        if (path.endsWith("/data:query")) return Response.json({ detail: "busy" }, { status: 503 });
        throw new Error(`Unexpected request: ${request.method} ${request.url}`);
      }),
    );
    renderAt(`/dashboards/${DASHBOARD_ID}/edit`);

    const add = await screen.findByLabelText("Add a filter");
    await waitFor(() => expect(within(add).getByRole("option", { name: /Region · list/ })).toBeInTheDocument());
    expect(within(add).queryByRole("option", { name: /Amount/ })).not.toBeInTheDocument();
    fireEvent.change(add, { target: { value: "values:region" } });
    expect(await screen.findByLabelText("Filter name")).toHaveValue("Region");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(saved).toMatchObject({
        definition: {
          schema_version: 2,
          filters: [{ label: "Region", kind: "values", targets: [{ item_id: ITEM_ID, field_id: REGION_FIELD }] }],
        },
      }),
    );
  });
});
