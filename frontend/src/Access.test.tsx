import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";

const ADMIN_ID = "00000000-0000-4000-8000-000000000001";
const ANALYST_ID = "00000000-0000-4000-8000-000000000002";
const GROUP_ID = "00000000-0000-4000-8000-000000000003";
const ACCOUNT_ID = "00000000-0000-4000-8000-000000000004";
const TOKEN_ID = "00000000-0000-4000-8000-000000000005";
const CREATED_ID = "00000000-0000-4000-8000-000000000006";
const NOW = "2026-10-16T08:00:00Z";

function renderAccess() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/access"]}><App /></MemoryRouter></QueryClientProvider>);
}

describe("access administration", () => {
  it("redirects non-administrators before loading access resources", async () => {
    const requests: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      const url = new URL(request.url);
      requests.push(url.pathname);
      if (url.pathname === "/api/v1/auth/me") {
        return Response.json({ id: ANALYST_ID, email: "ada@example.com", display_name: "Ada Analyst", role: "analyst" });
      }
      if (url.pathname === "/api/v1/dashboards") {
        return Response.json({ data: [], meta: { next_cursor: null } });
      }
      throw new Error(`Unexpected request: ${request.method} ${request.url}`);
    }));

    renderAccess();

    expect(await screen.findByRole("heading", { name: "Overview" })).toBeVisible();
    expect(requests).not.toContain("/api/v1/users");
    expect(requests).not.toContain("/api/v1/groups");
    expect(requests).not.toContain("/api/v1/service-accounts");
  });

  it("manages people, group membership, and one-time integration tokens", async () => {
    document.cookie = "datapipette_csrf=test-token; path=/";
    const copied: string[] = [];
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn(async (value: string) => { copied.push(value); }) } });
    let createdUserBody: unknown;
    let membershipAdded = false;
    let issued = false;
    let issuedBody: unknown;
    let revoked = false;
    const people = [
      { id: ADMIN_ID, email: "admin@example.com", display_name: "Yuriy Admin", role: "admin", status: "active", last_login_at: NOW, created_at: NOW, updated_at: NOW },
      { id: ANALYST_ID, email: "ada@example.com", display_name: "Ada Analyst", role: "analyst", status: "active", last_login_at: null, created_at: NOW, updated_at: NOW },
    ];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init); const url = new URL(request.url);
      if (url.pathname === "/api/v1/auth/me") return Response.json({ id: ADMIN_ID, email: "admin@example.com", display_name: "Yuriy Admin", role: "admin" });
      if (url.pathname === "/api/v1/users" && request.method === "GET") return Response.json({ data: people, meta: { next_cursor: null } });
      if (url.pathname === "/api/v1/users" && request.method === "POST") { createdUserBody = await request.json(); return Response.json({ id: CREATED_ID, ...(createdUserBody as object), status: "active", last_login_at: null, created_at: NOW, updated_at: NOW }, { status: 201 }); }
      if (url.pathname === "/api/v1/groups" && request.method === "GET") return Response.json({ data: [{ id: GROUP_ID, name: "Finance analysts", created_by: ADMIN_ID, created_at: NOW, updated_at: NOW }], meta: { limit: 100 } });
      if (url.pathname === `/api/v1/groups/${GROUP_ID}/members` && request.method === "GET") return Response.json({ data: membershipAdded ? [{ group_id: GROUP_ID, user_id: ANALYST_ID, created_by: ADMIN_ID, created_at: NOW }] : [] });
      if (url.pathname === `/api/v1/groups/${GROUP_ID}/members/${ANALYST_ID}` && request.method === "PUT") { membershipAdded = true; return Response.json({ group_id: GROUP_ID, user_id: ANALYST_ID, created_by: ADMIN_ID, created_at: NOW }); }
      if (url.pathname === "/api/v1/service-accounts" && request.method === "GET") return Response.json({ data: [{ id: ACCOUNT_ID, name: "CRM dashboard embed", role: "viewer", status: "active", created_at: NOW, updated_at: NOW }] });
      if (url.pathname === `/api/v1/service-accounts/${ACCOUNT_ID}/tokens` && request.method === "GET") return Response.json({ data: issued ? [{ id: TOKEN_ID, service_account_id: ACCOUNT_ID, name: "October rotation", scopes: ["dashboards:read"], expires_at: null, revoked_at: revoked ? NOW : null, last_used_at: null, created_by: ADMIN_ID, created_at: NOW }] : [] });
      if (url.pathname === `/api/v1/service-accounts/${ACCOUNT_ID}/tokens` && request.method === "POST") { issuedBody = await request.json(); issued = true; return Response.json({ id: TOKEN_ID, service_account_id: ACCOUNT_ID, ...(issuedBody as object), revoked_at: null, last_used_at: null, created_by: ADMIN_ID, created_at: NOW, token: "dpp_once_only_fixture" }, { status: 201 }); }
      if (url.pathname === `/api/v1/service-accounts/${ACCOUNT_ID}/tokens/${TOKEN_ID}` && request.method === "DELETE") { revoked = true; return new Response(null, { status: 204 }); }
      throw new Error(`Unexpected request: ${request.method} ${request.url}`);
    }));
    renderAccess();

    expect(await screen.findByRole("heading", { name: "Access" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Add person" }));
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Mira Viewer" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "mira@example.com" } });
    fireEvent.change(screen.getByLabelText("Initial password"), { target: { value: "temporary-safe-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Create person" }));
    await waitFor(() => expect(createdUserBody).toEqual({ email: "mira@example.com", display_name: "Mira Viewer", password: "temporary-safe-password", role: "viewer" }));

    fireEvent.click(screen.getByRole("button", { name: "Groups" }));
    expect(await screen.findByRole("heading", { name: "Finance analysts" })).toBeVisible();
    fireEvent.change(screen.getByLabelText("Person to add"), { target: { value: ANALYST_ID } });
    const addMember = screen.getByRole("button", { name: "Add member" });
    await waitFor(() => expect(addMember).toBeEnabled());
    fireEvent.click(addMember);
    await waitFor(() => expect(membershipAdded).toBe(true));
    expect(await screen.findByRole("button", { name: "Remove Ada Analyst" })).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Integrations" }));
    expect(await screen.findByRole("heading", { name: "CRM dashboard embed" })).toBeVisible();
    fireEvent.change(screen.getByLabelText("Token name"), { target: { value: "October rotation" } });
    fireEvent.change(screen.getByLabelText("Token expiry"), { target: { value: "never" } });
    fireEvent.click(screen.getByRole("button", { name: "Issue token" }));
    expect(await screen.findByText("dpp_once_only_fixture")).toBeVisible();
    expect(issuedBody).toEqual({ name: "October rotation", scopes: ["dashboards:read"], expires_at: null });
    fireEvent.click(screen.getByRole("button", { name: "Copy token" }));
    await waitFor(() => expect(copied).toEqual(["dpp_once_only_fixture"]));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss token" }));
    expect(screen.queryByText("dpp_once_only_fixture")).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Revoke October rotation" }));
    await waitFor(() => expect(revoked).toBe(true));
  });
});
