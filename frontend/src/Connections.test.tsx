import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";

const ADMIN_ID = "00000000-0000-4000-8000-000000000001";
const CONNECTION_ID = "00000000-0000-4000-8000-000000000002";
const ANALYST_ID = "00000000-0000-4000-8000-000000000003";
const GRANT_ID = "00000000-0000-4000-8000-000000000004";

const connection = {
  id: CONNECTION_ID,
  name: "Production warehouse",
  host: "warehouse.internal",
  port: 5432,
  database_name: "analytics",
  username: "datepipette_reader",
  ssl_mode: "verify-full",
  has_password: true,
  has_ca_certificate: true,
  created_by: ADMIN_ID,
  last_tested_at: null,
  last_test_succeeded: null,
  last_test_error_code: null,
  created_at: "2026-10-16T08:00:00Z",
  updated_at: "2026-10-16T08:00:00Z",
};

function renderApp(path: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}><App /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("connection administration", () => {
  it("creates a source while keeping its credentials out of returned state", async () => {
    document.cookie = "datepipette_csrf=test-token; path=/";
    let body: Record<string, unknown> | undefined;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      if (request.url.endsWith("/api/v1/auth/me")) return Response.json({ id: ADMIN_ID, email: "admin@example.com", display_name: "Admin", role: "admin" });
      if (request.url.endsWith("/api/v1/connections") && request.method === "POST") {
        body = await request.json() as Record<string, unknown>;
        return Response.json(connection, { status: 201 });
      }
      if (request.url.endsWith(`/api/v1/connections/${CONNECTION_ID}/catalog/schemas`)) return Response.json({ data: [], meta: { next_cursor: null } });
      if (request.url.endsWith(`/api/v1/connections/${CONNECTION_ID}/grants`)) return Response.json({ data: [] });
      if (request.url.includes("/api/v1/users?")) return Response.json({ data: [], meta: { next_cursor: null } });
      if (request.url.includes("/api/v1/groups?")) return Response.json({ data: [], meta: {} });
      throw new Error(`Unexpected request: ${request.method} ${request.url}`);
    }));
    renderApp("/connections/new");

    expect(await screen.findByRole("heading", { name: "Add a connection" })).toBeVisible();
    fireEvent.change(screen.getByLabelText("Connection name"), { target: { value: "Production warehouse" } });
    fireEvent.change(screen.getByLabelText("Host"), { target: { value: "warehouse.internal" } });
    fireEvent.change(screen.getByLabelText("Database"), { target: { value: "analytics" } });
    fireEvent.change(screen.getByLabelText("PostgreSQL role"), { target: { value: "datepipette_reader" } });
    fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: "one-time-secret" } });
    fireEvent.change(screen.getByLabelText(/^CA certificate/), { target: { value: "-----BEGIN CERTIFICATE-----\nfixture" } });
    fireEvent.click(screen.getByRole("button", { name: "Create connection" }));

    expect(await screen.findByRole("heading", { name: "Production warehouse" })).toBeVisible();
    expect(body).toMatchObject({ host: "warehouse.internal", database_name: "analytics", username: "datepipette_reader", password: "one-time-secret", ssl_mode: "verify-full" });
    expect(JSON.stringify(connection)).not.toContain("one-time-secret");
    expect(screen.getByLabelText(/^Replace password/)).toHaveValue("");
  });

  it("tests the source, inspects a relation, and grants exact table scope", async () => {
    document.cookie = "datepipette_csrf=test-token; path=/";
    let grantBody: unknown;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      const url = new URL(request.url);
      if (url.pathname === "/api/v1/auth/me") return Response.json({ id: ADMIN_ID, email: "admin@example.com", display_name: "Admin", role: "admin" });
      if (url.pathname === `/api/v1/connections/${CONNECTION_ID}`) return Response.json(connection);
      if (url.pathname.endsWith("/catalog/schemas")) return Response.json({ data: [{ name: "sales" }], meta: { next_cursor: null } });
      if (url.pathname.endsWith("/catalog/schemas/sales/relations")) return Response.json({ data: [{ schema_name: "sales", name: "orders", kind: "table" }], meta: { next_cursor: null } });
      if (url.pathname.endsWith("/catalog/schemas/sales/relations/orders")) return Response.json({ schema_name: "sales", name: "orders", kind: "table", columns: [{ name: "id", ordinal_position: 1, data_type: "bigint", nullable: false }], primary_key: ["id"], foreign_keys: [] });
      if (url.pathname.endsWith("/tests") && request.method === "POST") return Response.json({ succeeded: true, tested_at: "2026-10-16T09:00:00Z", error_code: null, server_version: 160004 });
      if (url.pathname.endsWith("/grants") && request.method === "GET") return Response.json({ data: grantBody ? [{ id: GRANT_ID, connection_id: CONNECTION_ID, principal_type: "user", principal_id: ANALYST_ID, scope_type: "table", schema_name: "sales", relation_name: "orders", created_by: ADMIN_ID, created_at: "2026-10-16T09:00:00Z" }] : [] });
      if (url.pathname.endsWith("/grants") && request.method === "POST") { grantBody = await request.json(); return Response.json({ id: GRANT_ID, connection_id: CONNECTION_ID, ...(grantBody as object), created_by: ADMIN_ID, created_at: "2026-10-16T09:00:00Z" }, { status: 201 }); }
      if (url.pathname === "/api/v1/users") return Response.json({ data: [{ id: ANALYST_ID, email: "ada@example.com", display_name: "Ada Analyst", role: "analyst", status: "active", last_login_at: null, created_at: "2026-10-16T08:00:00Z", updated_at: "2026-10-16T08:00:00Z" }], meta: { next_cursor: null } });
      if (url.pathname === "/api/v1/groups") return Response.json({ data: [], meta: {} });
      throw new Error(`Unexpected request: ${request.method} ${request.url}`);
    }));
    renderApp(`/connections/${CONNECTION_ID}`);

    expect(await screen.findByRole("heading", { name: "Production warehouse" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Test saved settings" }));
    expect(await screen.findByText("Connection succeeded")).toBeVisible();
    fireEvent.click(await screen.findByRole("button", { name: /orders/ }));
    expect(await screen.findByText("1 columns")).toBeVisible();
    fireEvent.change(screen.getByLabelText("Visible scope"), { target: { value: "table" } });
    fireEvent.click(screen.getByRole("button", { name: "Grant source access" }));
    await waitFor(() => expect(grantBody).toEqual({ principal_type: "user", principal_id: ANALYST_ID, scope_type: "table", schema_name: "sales", relation_name: "orders" }));
    expect(await screen.findByText("Ada Analyst")).toBeVisible();
  });
});
