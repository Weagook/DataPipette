import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { AppShell } from "./components/AppShell";
import { FullPageStatus } from "./components/Status";

const admin = {
  id: "00000000-0000-4000-8000-000000000001",
  email: "admin@example.com",
  display_name: "Local Administrator",
  role: "admin" as const,
  locale: "en" as const,
};

function renderShell(path = "/governance") {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <AppShell user={admin}><h1>Governance content</h1></AppShell>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("cross-cutting accessibility", () => {
  it("provides bypass navigation, route context, and an explicit profile name", async () => {
    renderShell();

    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main-content");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
    expect(screen.getByRole("button", { name: "Open profile menu for Local Administrator" })).toHaveAttribute("aria-controls", "profile-menu");
    await waitFor(() => expect(document.title).toBe("Governance · DatePipette"));
  });

  it("moves focus into the mobile navigation and restores it on Escape", async () => {
    renderShell();
    const opener = screen.getByRole("button", { name: "Open navigation" });
    fireEvent.click(opener);

    const closer = screen.getByRole("button", { name: "Close navigation" });
    await waitFor(() => expect(closer).toHaveFocus());
    expect(opener).toHaveAttribute("aria-expanded", "true");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(opener).toHaveAttribute("aria-expanded", "false");
    expect(opener).toHaveFocus();
  });

  it("announces blocking and recoverable full-page states", () => {
    const { rerender } = render(<FullPageStatus label="Opening workspace" />);
    expect(screen.getByRole("main")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");

    rerender(<FullPageStatus label="Unavailable" detail="Try later" actionLabel="Retry" onAction={() => undefined} />);
    expect(screen.getByRole("alert")).toHaveAttribute("aria-live", "assertive");
  });
});
