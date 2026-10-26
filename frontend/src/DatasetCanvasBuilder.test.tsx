import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { DatasetCanvasBuilder } from "./components/DatasetCanvasBuilder";
import { I18nProvider } from "./lib/i18n";
import type { VisualDefinition } from "./lib/studioDefinition";

const ROOT_ID = "00000000-0000-4000-8000-000000000001";

function initialDefinition(): VisualDefinition {
  return {
    kind: "visual",
    schema_version: 2,
    source: {
      relations: [
        { id: ROOT_ID, schema: "sales", name: "orders", alias: "orders" },
      ],
    },
    fields: [
      {
        id: "00000000-0000-4000-8000-000000000011",
        source: { relation_id: ROOT_ID, column: "id" },
        name: "order_id",
        display: { label: "Order ID", format: null, visible: true },
      },
    ],
    joins: [],
  };
}

describe("dataset canvas builder", () => {
  it("does not mutate a saved definition when canvas mode opens", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const onChange = vi.fn();

    render(
      <QueryClientProvider client={client}>
        <I18nProvider>
          <DatasetCanvasBuilder
            connectionId="00000000-0000-4000-8000-000000000099"
            definition={initialDefinition()}
            onChange={onChange}
          />
        </I18nProvider>
      </QueryClientProvider>,
    );

    expect((await screen.findAllByText("Dataset Output"))[0]).toBeInTheDocument();
    await waitFor(() => expect(onChange).not.toHaveBeenCalled());
  });

  it("edits the authoritative definition and supports undo", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    function Harness() {
      const [definition, setDefinition] = useState(initialDefinition);
      return (
        <DatasetCanvasBuilder
          connectionId="00000000-0000-4000-8000-000000000099"
          definition={definition}
          onChange={setDefinition}
        />
      );
    }

    render(
      <QueryClientProvider client={client}>
        <I18nProvider>
          <Harness />
        </I18nProvider>
      </QueryClientProvider>,
    );

    expect((await screen.findAllByText("Dataset Output"))[0]).toBeInTheDocument();
    fireEvent.click(screen.getByText("orders"));
    const alias = await screen.findByLabelText("Relation alias");
    fireEvent.change(alias, { target: { value: "sales_orders" } });
    expect(screen.getByDisplayValue("sales_orders")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(screen.getByDisplayValue("orders")).toBeVisible());
  });
});
