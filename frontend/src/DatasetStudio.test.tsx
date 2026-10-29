import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";

const CONNECTION_ID = "00000000-0000-4000-8000-000000000010";
const DATASET_ID = "00000000-0000-4000-8000-000000000011";

describe("dataset studio", () => {
  it("creates an advanced SQL dataset through the public API", async () => {
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
    fireEvent.click(screen.getByRole("radio", { name: /SQL \(for specialists\)/ }));
    fireEvent.change(
      await screen.findByLabelText("Read-only PostgreSQL query"),
      { target: { value: "SELECT id FROM public.orders" } },
    );
    fireEvent.change(screen.getByLabelText("SQL result name"), {
      target: { value: "id" },
    });
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

  it("opens the complete visual builder before creating a dataset", async () => {
    document.cookie = "datapipette_csrf=test-token; path=/";
    let createBody: unknown;
    let catalogRequests = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const request = new Request(input, init);
        const url = new URL(request.url);
        if (url.pathname === "/api/v1/auth/me")
          return Response.json({
            id: "user-1",
            email: "ada@example.com",
            display_name: "Ada Analyst",
            role: "analyst",
          });
        if (url.pathname === "/api/v1/connections")
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
        if (url.pathname.endsWith("/catalog/schemas")) {
          catalogRequests += 1;
          return Response.json({
            data: [{ name: "public" }],
            meta: { next_cursor: null },
          });
        }
        if (url.pathname.endsWith("/catalog/schemas/public/relations"))
          return Response.json({
            data: [
              { schema_name: "public", name: "orders", kind: "table" },
            ],
            meta: { next_cursor: null },
          });
        if (url.pathname.endsWith("/catalog/schemas/public/relations/orders"))
          return Response.json({
            schema_name: "public",
            name: "orders",
            kind: "table",
            primary_key: ["id"],
            foreign_keys: [],
            columns: [
              {
                name: "id",
                data_type: "bigint",
                nullable: false,
                ordinal_position: 1,
              },
              {
                name: "customer_id",
                data_type: "bigint",
                nullable: false,
                ordinal_position: 2,
              },
            ],
          });
        if (url.pathname === "/api/v1/datasets" && request.method === "POST") {
          createBody = await request.json();
          return Response.json(
            {
              id: DATASET_ID,
              name: "Orders model",
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
        if (url.pathname === `/api/v1/datasets/${DATASET_ID}`)
          return Response.json({
            id: DATASET_ID,
            name: "Orders model",
            owner_id: "user-1",
            connection_id: CONNECTION_ID,
            active_version_id: null,
            active_snapshot_id: null,
            draft_revision: 1,
            created_at: "2026-10-16T00:00:00Z",
            updated_at: "2026-10-16T00:00:00Z",
          });
        if (url.pathname === `/api/v1/datasets/${DATASET_ID}/draft`)
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

    await screen.findByRole("heading", { name: "New dataset" });
    fireEvent.change(screen.getByLabelText("Dataset name"), {
      target: { value: "Orders model" },
    });
    await screen.findByRole("option", { name: "Warehouse · analytics" });
    expect(
      screen.getByText("Choose a connection to start building"),
    ).toBeVisible();
    expect(catalogRequests).toBe(0);
    fireEvent.change(screen.getByLabelText("PostgreSQL connection"), {
      target: { value: CONNECTION_ID },
    });
    fireEvent.change(screen.getByLabelText("Schema"), {
      target: { value: "public" },
    });
    await screen.findByRole("option", { name: "orders · table" });
    fireEvent.change(screen.getByLabelText("Table or view"), {
      target: { value: "orders" },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Use table" }));

    expect(await screen.findByText("2. Build the visual model")).toBeVisible();
    expect(screen.getByText("Source columns")).toBeVisible();
    const customer = await screen.findByRole("checkbox", {
      name: /customer_id/,
    });
    expect(customer).toBeChecked();
    fireEvent.click(customer);
    fireEvent.change(screen.getAllByLabelText("Output name")[0], {
      target: { value: "order_id" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create dataset" }));

    await waitFor(() =>
      expect(createBody).toMatchObject({
        name: "Orders model",
        connection_id: CONNECTION_ID,
        definition: {
          kind: "visual",
          source: { relations: [{ schema: "public", name: "orders" }] },
          fields: [{ name: "order_id", source: { column: "id" } }],
        },
      }),
    );
  });

  it("lets the analyst retry a failed source catalog", async () => {
    let schemaRequests = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = new Request(input);
        const url = new URL(request.url);
        if (url.pathname === "/api/v1/auth/me")
          return Response.json({
            id: "user-1",
            email: "ada@example.com",
            display_name: "Ada Analyst",
            role: "analyst",
          });
        if (url.pathname === "/api/v1/connections")
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
        if (url.pathname.endsWith("/catalog/schemas")) {
          schemaRequests += 1;
          if (schemaRequests === 1)
            return Response.json(
              {
                code: "catalog_unavailable",
                title: "Catalog unavailable",
                detail: "The PostgreSQL source catalog could not be read.",
              },
              { status: 502 },
            );
          return Response.json({
            data: [{ name: "public" }],
            meta: { next_cursor: null },
          });
        }
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

    await screen.findByRole("option", { name: "Warehouse · analytics" });
    fireEvent.change(screen.getByLabelText("PostgreSQL connection"), {
      target: { value: CONNECTION_ID },
    });
    expect(
      await screen.findByText(
        "The PostgreSQL source catalog could not be read.",
      ),
    ).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("option", { name: "public" })).toBeVisible();
    expect(schemaRequests).toBe(2);
  });
});
