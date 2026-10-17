import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";

const ADMIN_ID = "00000000-0000-4000-8000-000000000001";
const EVENT_ID = "00000000-0000-4000-8000-000000000002";

const policy = {
  estimated_rows_warning: 100_000,
  estimated_rows_block: 1_000_000,
  total_cost_warning: 10_000,
  total_cost_block: 100_000,
  join_count_warning: 5,
  join_count_block: 10,
  sequential_scan_rows_warning: 100_000,
  sort_rows_warning: 100_000,
  stale_statistics_days: 14,
  preview_statement_timeout_seconds: 10,
  preview_max_rows: 100,
  preview_max_response_bytes: 524_288,
  refresh_statement_timeout_seconds: 3600,
  refresh_max_rows: 5_000_000,
  interactive_query_timeout_seconds: 15,
  interactive_query_memory_limit_mb: 512,
  interactive_query_threads: 4,
  interactive_query_concurrency: 8,
  interactive_query_temp_limit_mb: 1024,
  interactive_query_max_response_bytes: 4_194_304,
  export_timeout_seconds: 3600,
  export_memory_limit_mb: 1024,
  export_threads: 4,
  export_temp_limit_mb: 4096,
  export_max_rows: 1_000_000,
  export_max_bytes: 1_073_741_824,
  background_job_concurrency: 8,
  revision: 3,
  updated_at: "2026-10-17T08:00:00Z",
  updated_by: ADMIN_ID,
};

function renderApp(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><App /></MemoryRouter></QueryClientProvider>);
}

function currentUser(role: "admin" | "viewer") {
  return { id: ADMIN_ID, email: `${role}@example.com`, display_name: role === "admin" ? "Admin" : "Viewer", role };
}

describe("governance administration", () => {
  it("redirects non-admin users without calling governance APIs", async () => {
    const requests: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init); requests.push(request.url);
      if (request.url.endsWith("/api/v1/auth/me")) return Response.json(currentUser("viewer"));
      if (request.url.includes("/api/v1/dashboards")) return Response.json({ data: [], meta: { next_cursor: null } });
      throw new Error(`Unexpected request: ${request.method} ${request.url}`);
    }));
    renderApp("/governance");
    expect(await screen.findByRole("heading", { name: "Good data should feel quiet." })).toBeVisible();
    expect(requests.some((url) => url.includes("query-safety-policy") || url.includes("audit-events"))).toBe(false);
  });

  it("validates and replaces the complete policy with ETag and CSRF protection", async () => {
    document.cookie = "datepipette_csrf=test-token; path=/";
    let putRequest: Request | undefined;
    let putBody: Record<string, number> | undefined;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      if (request.url.endsWith("/api/v1/auth/me")) return Response.json(currentUser("admin"));
      if (request.url.endsWith("/api/v1/query-safety-policy") && request.method === "GET") return Response.json(policy, { headers: { ETag: '"query-safety-policy-3"' } });
      if (request.url.endsWith("/api/v1/query-safety-policy") && request.method === "PUT") {
        putRequest = request; putBody = await request.json() as Record<string, number>;
        return Response.json({ ...policy, ...putBody, revision: 4, updated_at: "2026-10-17T09:00:00Z" }, { headers: { ETag: '"query-safety-policy-4"' } });
      }
      throw new Error(`Unexpected request: ${request.method} ${request.url}`);
    }));
    renderApp("/governance");
    expect(await screen.findByText("Query Safety policy")).toBeVisible();

    fireEvent.change(screen.getByLabelText("Estimated rows warning"), { target: { value: "1000000" } });
    expect(screen.getByText("Must be below the block threshold")).toBeVisible();
    expect(screen.getByRole("button", { name: "Save complete policy" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Estimated rows warning"), { target: { value: "200000" } });
    fireEvent.click(screen.getByRole("button", { name: "Save complete policy" }));

    expect(await screen.findByText("Policy revision 4 is active.")).toBeVisible();
    expect(putRequest?.headers.get("If-Match")).toBe('"query-safety-policy-3"');
    expect(putRequest?.headers.get("X-CSRF-Token")).toBe("test-token");
    expect(putBody?.estimated_rows_warning).toBe(200_000);
    expect(Object.keys(putBody ?? {})).toHaveLength(27);
  });

  it("filters, inspects, and cursor-paginates audit history", async () => {
    const auditUrls: URL[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      const url = new URL(request.url);
      if (url.pathname === "/api/v1/auth/me") return Response.json(currentUser("admin"));
      if (url.pathname === "/api/v1/query-safety-policy") return Response.json(policy, { headers: { ETag: '"query-safety-policy-3"' } });
      if (url.pathname === "/api/v1/audit-events") {
        auditUrls.push(url);
        const cursor = url.searchParams.get("cursor");
        return Response.json({
          data: [{ id: cursor ? `${EVENT_ID.slice(0, -1)}3` : EVENT_ID, occurred_at: cursor ? "2026-10-16T08:00:00Z" : "2026-10-17T08:00:00Z", actor_type: "user", actor_id: ADMIN_ID, action: url.searchParams.get("action") || "query_safety.policy_replaced", outcome: "success", resource_type: "query_safety_policy", resource_id: null, request_id: "request-42", details: { revision: cursor ? 2 : 3, source: "browser" } }],
          meta: { next_cursor: cursor ? null : "older-page" },
        });
      }
      throw new Error(`Unexpected request: ${request.method} ${request.url}`);
    }));
    renderApp("/governance");
    fireEvent.click(await screen.findByRole("button", { name: "Audit history" }));
    expect(await screen.findByText("query_safety.policy_replaced")).toBeVisible();

    fireEvent.change(screen.getByLabelText("Audit action"), { target: { value: "dataset.refresh_started" } });
    fireEvent.change(screen.getByLabelText("Audit outcome"), { target: { value: "success" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    expect(await screen.findByText("dataset.refresh_started")).toBeVisible();
    await waitFor(() => expect(auditUrls.at(-1)?.searchParams.get("action")).toBe("dataset.refresh_started"));
    expect(auditUrls.at(-1)?.searchParams.get("outcome")).toBe("success");

    fireEvent.click(screen.getByText("dataset.refresh_started"));
    expect(await screen.findByText(/"source": "browser"/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Load older events" }));
    await waitFor(() => expect(auditUrls.at(-1)?.searchParams.get("cursor")).toBe("older-page"));
    expect(await screen.findByText("2 loaded")).toBeVisible();
  });
});
