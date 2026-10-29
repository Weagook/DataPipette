import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";

const DASHBOARD_ID = "00000000-0000-4000-8000-000000000101";
const ITEM_ID = "00000000-0000-4000-8000-000000000102";

describe("dashboard reading", () => {
  it("refreshes the layout and every chart item", async () => {
    let layoutRequests = 0;
    let itemRequests = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const request = new Request(input, init);
        const path = new URL(request.url).pathname;
        if (path === "/api/v1/auth/me")
          return Response.json({ id: "user-1", email: "ada@example.com", display_name: "Ada", role: "viewer" });
        if (path === `/api/v1/dashboards/${DASHBOARD_ID}`) {
          layoutRequests += 1;
          return Response.json({
            id: DASHBOARD_ID,
            name: "Sales",
            owner_id: "owner-1",
            revision: 1,
            created_at: "2026-10-16T00:00:00Z",
            updated_at: "2026-10-16T00:00:00Z",
            definition: {
              schema_version: 1,
              items: [{ id: ITEM_ID, chart_id: "00000000-0000-4000-8000-000000000103", x: 0, y: 0, width: 6, height: 2 }],
            },
          });
        }
        if (path === `/api/v1/dashboards/${DASHBOARD_ID}/items/${ITEM_ID}/data:query`) {
          itemRequests += 1;
          return Response.json({ detail: "busy" }, { status: 503 });
        }
        throw new Error(`Unexpected request: ${request.method} ${request.url}`);
      }),
    );
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={[`/dashboards/${DASHBOARD_ID}`]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const refresh = await screen.findByRole("button", { name: /Refresh charts/ });
    // A short tile keeps the height set in the editor, so it never overlaps the row below.
    expect(document.querySelector<HTMLElement>(".chart-card")?.style.gridRow).toBe("1 / span 2");
    await waitFor(() => expect(itemRequests).toBe(1));
    await waitFor(() => expect(refresh).toBeEnabled());
    fireEvent.click(refresh);

    await waitFor(() => expect(itemRequests).toBe(2));
    expect(layoutRequests).toBe(2);
    expect(screen.queryByRole("button", { name: "More dashboard actions" })).not.toBeInTheDocument();
  });
});
