import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ExportPanel } from "./ExportPanel";

describe("export downloads", () => {
  it("starts an explicit same-origin browser download from a fresh capability", async () => {
    document.cookie = "datapipette_csrf=test-csrf";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = new URL(new Request(input).url);
        if (url.pathname.endsWith("/exports")) {
          return Response.json({
            data: [
              {
                id: "00000000-0000-4000-8000-000000000004",
                format: "csv",
                state: "succeeded",
                row_count: 2,
                requested_at: "2026-10-17T10:00:00Z",
                expires_at: "2099-10-18T10:00:00Z",
              },
            ],
            meta: { count: 1 },
          });
        }
        if (url.pathname.endsWith("/download-link")) {
          return Response.json({
            download_url: "/api/v1/export-downloads?token=fresh-capability",
            expires_at: "2099-10-17T10:05:00Z",
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    let clickedAnchor: HTMLAnchorElement | undefined;
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clickedAnchor = this;
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <ExportPanel
          datasetId="00000000-0000-4000-8000-000000000001"
          query={{ fields: [], filters: [], sorts: [] }}
        />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Export" }));
    fireEvent.click(await screen.findByRole("button", { name: "Download" }));

    await waitFor(() => expect(clickedAnchor).toBeDefined());
    expect(clickedAnchor?.download).toBe("");
    expect(clickedAnchor?.pathname).toBe("/api/v1/export-downloads");
    expect(clickedAnchor?.search).toBe("?token=fresh-capability");
  });
});
