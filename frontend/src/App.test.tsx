import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";

function renderApp(path: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("application session flow", () => {
  it("redirects an unauthenticated protected route to login", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          {
            code: "authentication_required",
            title: "Authentication required",
            detail: "Sign in.",
          },
          { status: 401 },
        ),
      ),
    );
    renderApp("/dashboards");
    expect(
      await screen.findByRole(
        "heading",
        { name: "Sign in to DatePipette" },
        { timeout: 5_000 },
      ),
    ).toBeVisible();
  });

  it("signs in without persisting credentials and opens the workspace", async () => {
    const requestBodies: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const request = new Request(input, init);
        if (request.url.endsWith("/api/v1/auth/sessions")) {
          requestBodies.push(await request.json());
          return Response.json(
            {
              user: {
                id: "1",
                email: "analyst@example.com",
                display_name: "Ada Analyst",
                role: "analyst",
                locale: "en",
              },
              expires_at: "2026-10-16T10:00:00Z",
            },
            { status: 201 },
          );
        }
        if (request.url.includes("/api/v1/dashboards")) {
          return Response.json({ data: [], meta: { next_cursor: null } });
        }
        throw new Error(`Unexpected request: ${request.url}`);
      }),
    );

    renderApp("/login");
    fireEvent.change(await screen.findByLabelText("Work email"), {
      target: { value: "analyst@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "not-persisted" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Open workspace" }));

    expect(
      await screen.findByRole("heading", {
        name: "Good data should feel quiet.",
      }),
    ).toBeVisible();
    expect(requestBodies).toEqual([
      { email: "analyst@example.com", password: "not-persisted" },
    ]);
    await waitFor(() => expect(screen.getByText("Ada Analyst")).toBeVisible());
    expect(localStorage.length).toBe(0);
  });

  it("persists the profile language and switches the interface", async () => {
    const preferenceBodies: unknown[] = [];
    document.cookie = "datepipette_csrf=test-csrf-token; path=/";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const request = new Request(input, init);
        if (request.url.endsWith("/api/v1/auth/me") && request.method === "GET") {
          return Response.json({
            id: "1",
            email: "admin@example.com",
            display_name: "Local Administrator",
            role: "admin",
            locale: "en",
          });
        }
        if (request.url.includes("/api/v1/auth/me/preferences")) {
          preferenceBodies.push(await request.json());
          return Response.json({
            id: "1",
            email: "admin@example.com",
            display_name: "Local Administrator",
            role: "admin",
            locale: "ru",
          });
        }
        if (request.url.includes("/api/v1/dashboards")) {
          return Response.json({ data: [], meta: { next_cursor: null } });
        }
        throw new Error(`Unexpected request: ${request.url}`);
      }),
    );

    renderApp("/overview");
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Open profile menu for Local Administrator",
      }),
    );
    fireEvent.change(screen.getByLabelText("Interface language"), {
      target: { value: "ru" },
    });

    expect(await screen.findByText("Дашборды")).toBeVisible();
    await waitFor(() => expect(preferenceBodies).toEqual([{ locale: "ru" }]));
    expect(screen.getByLabelText("Язык интерфейса")).toHaveValue("ru");
  });
});
