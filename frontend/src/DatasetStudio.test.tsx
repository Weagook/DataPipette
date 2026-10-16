import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";

const CONNECTION_ID = "00000000-0000-4000-8000-000000000010";
const DATASET_ID = "00000000-0000-4000-8000-000000000011";

describe("dataset studio", () => {
  it("creates an advanced SQL dataset through the public API", async () => {
    document.cookie = "datepipette_csrf=test-token; path=/";
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
        if (request.url.includes("/api/v1/connections?"))
          return Response.json({
            data: [
              {
                id: CONNECTION_ID,
                name: "Warehouse",
                database_name: "analytics",
                host: "db",
                port: 5432,
                username: "reader",
                ssl_mode: "require",
                has_password: true,
                has_ca_certificate: false,
                created_by: "user-1",
                last_tested_at: null,
                last_test_succeeded: null,
                last_test_error_code: null,
                created_at: "2026-10-16T00:00:00Z",
                updated_at: "2026-10-16T00:00:00Z",
              },
            ],
            meta: { next_cursor: null },
          });
        if (
          request.url.endsWith("/api/v1/datasets") &&
          request.method === "POST"
        ) {
          createBody = await request.json();
          return Response.json(
            {
              id: DATASET_ID,
              name: "Revenue SQL",
              owner_id: "user-1",
              connection_id: CONNECTION_ID,
              active_version_id: null,
              active_snapshot_id: null,
              draft_revision: 1,
              created_at: "2026-10-16T00:00:00Z",
              updated_at: "2026-10-16T00:00:00Z",
            },
            { status: 201, headers: { ETag: '"draft-1"' } },
          );
        }
        if (request.url.endsWith(`/api/v1/datasets/${DATASET_ID}`))
          return Response.json({
            id: DATASET_ID,
            name: "Revenue SQL",
            owner_id: "user-1",
            connection_id: CONNECTION_ID,
            active_version_id: null,
            active_snapshot_id: null,
            draft_revision: 1,
            created_at: "2026-10-16T00:00:00Z",
            updated_at: "2026-10-16T00:00:00Z",
          });
        if (request.url.endsWith(`/api/v1/datasets/${DATASET_ID}/draft`))
          return Response.json(
            {
              id: "draft-1",
              dataset_id: DATASET_ID,
              revision: 1,
              definition: (createBody as { definition: unknown }).definition,
              validation: null,
              created_by: "user-1",
              created_at: "2026-10-16T00:00:00Z",
              updated_at: "2026-10-16T00:00:00Z",
            },
            { headers: { ETag: '"draft-1"' } },
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
        <MemoryRouter initialEntries={["/datasets/new"]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(
      await screen.findByRole(
        "heading",
        { name: "New dataset" },
        { timeout: 5_000 },
      ),
    ).toBeVisible();
    fireEvent.change(screen.getByLabelText("Dataset name"), {
      target: { value: "Revenue SQL" },
    });
    await screen.findByRole("option", { name: "Warehouse · analytics" });
    fireEvent.change(await screen.findByLabelText("PostgreSQL connection"), {
      target: { value: CONNECTION_ID },
    });
    fireEvent.click(screen.getByRole("button", { name: /Advanced SQL/ }));
    fireEvent.change(
      await screen.findByLabelText("Read-only PostgreSQL query"),
      { target: { value: "SELECT id FROM public.orders" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Create dataset" }));

    await waitFor(() =>
      expect(createBody).toMatchObject({
        name: "Revenue SQL",
        connection_id: CONNECTION_ID,
        definition: {
          kind: "sql",
          statement: "SELECT id FROM public.orders",
          fields: [{ name: "id" }],
        },
      }),
    );
  });
});
