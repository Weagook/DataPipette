import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";

const USER_ID = "00000000-0000-4000-8000-000000000001";
const CHART_ID = "00000000-0000-4000-8000-000000000002";
const DATASET_ID = "00000000-0000-4000-8000-000000000003";
const DASHBOARD_ID = "00000000-0000-4000-8000-000000000004";
const RECIPIENT_ID = "00000000-0000-4000-8000-000000000005";

describe("dashboard studio", () => {
  it("creates a valid private layout and explicitly grants view access", async () => {
    document.cookie = "datapipette_csrf=test-token; path=/";
    let createBody: Record<string, unknown> | undefined;
    let grantBody: unknown;
    let createdDefinition: unknown;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const request = new Request(input, init);
        if (request.url.endsWith("/api/v1/auth/me"))
          return Response.json({
            id: USER_ID,
            email: "ada@example.com",
            display_name: "Ada Analyst",
            role: "analyst",
          });
        if (request.url.includes("/api/v1/charts?"))
          return Response.json({
            data: [
              {
                id: CHART_ID,
                dataset_id: DATASET_ID,
                owner_id: USER_ID,
                name: "Revenue by region",
                revision: 2,
                definition: {
                  schema_version: 2,
                  chart_type: "bar",
                  dimensions: [],
                  measures: [
                    {
                      id: "00000000-0000-4000-8000-000000000006",
                      field_id: "00000000-0000-4000-8000-000000000007",
                      aggregation: "sum",
                      label: "Revenue",
                    },
                  ],
                  filters: [],
                  sorts: [],
                  limit: 100,
                },
                created_at: "2026-10-16T00:00:00Z",
                updated_at: "2026-10-16T00:00:00Z",
              },
            ],
            meta: { next_cursor: null },
          });
        if (
          request.url.endsWith("/api/v1/dashboards") &&
          request.method === "POST"
        ) {
          createBody = (await request.json()) as Record<string, unknown>;
          createdDefinition = createBody.definition;
          return Response.json(
            {
              id: DASHBOARD_ID,
              owner_id: USER_ID,
              name: createBody.name,
              revision: 1,
              definition: createdDefinition,
              created_at: "2026-10-16T00:00:00Z",
              updated_at: "2026-10-16T00:00:00Z",
            },
            { status: 201, headers: { ETag: '"dashboard-1"' } },
          );
        }
        if (
          request.url.endsWith(`/api/v1/dashboards/${DASHBOARD_ID}/grants`) &&
          request.method === "GET"
        )
          return Response.json({ data: [] });
        if (
          request.url.endsWith(`/api/v1/dashboards/${DASHBOARD_ID}/grants`) &&
          request.method === "POST"
        ) {
          grantBody = await request.json();
          return Response.json(
            {
              id: "00000000-0000-4000-8000-000000000008",
              dashboard_id: DASHBOARD_ID,
              principal_type: "user",
              principal_id: RECIPIENT_ID,
              created_by: USER_ID,
              created_at: "2026-10-16T00:00:00Z",
            },
            { status: 201 },
          );
        }
        if (
          request.url.endsWith(`/api/v1/dashboards/${DASHBOARD_ID}`) &&
          request.method === "GET"
        )
          return Response.json(
            {
              id: DASHBOARD_ID,
              owner_id: USER_ID,
              name: "Executive revenue",
              revision: 1,
              definition: createdDefinition,
              created_at: "2026-10-16T00:00:00Z",
              updated_at: "2026-10-16T00:00:00Z",
            },
            { headers: { ETag: '"dashboard-1"' } },
          );
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
        <MemoryRouter initialEntries={["/dashboards/new"]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(
      await screen.findByLabelText("Dashboard name"),
    ).toBeVisible();
    fireEvent.change(screen.getByLabelText("Dashboard name"), {
      target: { value: "Executive revenue" },
    });
    fireEvent.click(
      await screen.findByRole("button", { name: "Add Revenue by region" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Create dashboard" }));

    await screen.findByRole("link", { name: "Open dashboard" });
    expect(createBody).toMatchObject({
      name: "Executive revenue",
      definition: {
        schema_version: 2,
        items: [
          { chart_id: CHART_ID, x: 0, y: 0, width: 6, height: 4 },
        ],
      },
    });
    fireEvent.change(screen.getByPlaceholderText("user UUID"), {
      target: { value: RECIPIENT_ID },
    });
    fireEvent.click(screen.getByRole("button", { name: "Grant view access" }));
    await waitFor(() =>
      expect(grantBody).toEqual({
        principal_type: "user",
        principal_id: RECIPIENT_ID,
      }),
    );
  });
});
