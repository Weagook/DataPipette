import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { DatasetCanvasBuilder } from "./components/DatasetCanvasBuilder";
import { I18nProvider } from "./lib/i18n";
import type { VisualDefinition } from "./lib/studioDefinition";

const CONNECTION_ID = "00000000-0000-4000-8000-000000000099";
const ROOT_ID = "00000000-0000-4000-8000-000000000001";

const TABLES: Record<string, { name: string; data_type: string }[]> = {
  orders: [
    { name: "id", data_type: "bigint" },
    { name: "customer_id", data_type: "bigint" },
    { name: "amount", data_type: "numeric(12,2)" },
  ],
  customers: [
    { name: "id", data_type: "bigint" },
    { name: "name", data_type: "text" },
  ],
  notes: [
    { name: "body", data_type: "text" },
    { name: "created_at", data_type: "timestamp with time zone" },
  ],
};

function stubCatalog() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      const path = new URL(request.url).pathname;
      if (path.endsWith("/catalog/schemas"))
        return Response.json({ data: [{ name: "public" }], meta: { next_cursor: null } });
      if (path.endsWith("/catalog/schemas/public/relations"))
        return Response.json({
          data: Object.keys(TABLES).map((name) => ({ schema_name: "public", name, kind: "table" })),
          meta: { next_cursor: null },
        });
      const table = path.match(/\/relations\/([a-z]+)$/)?.[1];
      if (table && TABLES[table])
        return Response.json({
          schema_name: "public",
          name: table,
          kind: "table",
          primary_key: [],
          foreign_keys: [],
          columns: TABLES[table].map((column, index) => ({
            ...column,
            nullable: true,
            ordinal_position: index + 1,
          })),
        });
      if (path.endsWith("/catalog/join-suggestions")) {
        const body = (await request.json()) as {
          relations: { id: string; name: string }[];
        };
        const orders = body.relations.find((item) => item.name === "orders");
        const customers = body.relations.find((item) => item.name === "customers");
        return Response.json({
          data:
            orders && customers
              ? [
                  {
                    foreign_key_name: "orders_customer",
                    source_relation_id: orders.id,
                    target_relation_id: customers.id,
                    conditions: [{ source_column: "customer_id", target_column: "id" }],
                  },
                ]
              : [],
        });
      }
      return Response.json({ detail: "not found" }, { status: 404 });
    }),
  );
}

function savedDefinition(): VisualDefinition {
  return {
    kind: "visual",
    schema_version: 2,
    source: { relations: [{ id: ROOT_ID, schema: "public", name: "orders", alias: "orders" }] },
    fields: [
      {
        id: "00000000-0000-4000-8000-000000000011",
        source: { relation_id: ROOT_ID, column: "id" },
        name: "id",
        display: { label: "Id", format: null, visible: true },
      },
    ],
    joins: [],
  };
}

// React Flow keeps nodes visibility-hidden until it can measure them, which
// jsdom never does, so controls inside table cards are found by their labels.
function renderBoard(initial: VisualDefinition | null, onChange = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const latest: { current: VisualDefinition | null } = { current: initial };
  function Harness() {
    const [definition, setDefinition] = useState(initial);
    return (
      <DatasetCanvasBuilder
        connectionId={CONNECTION_ID}
        definition={definition}
        onChange={(next) => {
          latest.current = next;
          onChange(next);
          setDefinition(next);
        }}
        allowEmpty
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
  return latest;
}

describe("dataset board", () => {
  it("opens a saved dataset without changing it", async () => {
    stubCatalog();
    const onChange = vi.fn();
    renderBoard(savedDefinition(), onChange);

    expect(await screen.findByText("Main table")).toBeInTheDocument();
    expect(await screen.findByLabelText("customer_id")).not.toBeChecked();
    await waitFor(() => expect(onChange).not.toHaveBeenCalled());
  });

  it("builds a linked dataset from tables added to the board", async () => {
    stubCatalog();
    const latest = renderBoard(null);
    expect(screen.getByText("Drag the first table here")).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Add orders to the board" }));
    await waitFor(() => expect(latest.current?.fields).toHaveLength(3));

    fireEvent.click(screen.getByRole("button", { name: "Add customers to the board" }));
    await waitFor(() => expect(latest.current?.joins).toHaveLength(1));
    expect(latest.current?.joins?.[0]).toMatchObject({
      type: "left",
      conditions: [{ left_column: "customer_id", right_column: "id" }],
    });
    expect(latest.current?.fields).toHaveLength(3);
    expect(await screen.findByText(/Linked automatically/)).toBeInTheDocument();

    fireEvent.click(await screen.findByLabelText("name"));
    await waitFor(() => expect(latest.current?.fields).toHaveLength(4));

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(latest.current?.fields).toHaveLength(3));
  });

  it("keeps a table without a known link on the board until it is linked", async () => {
    stubCatalog();
    const latest = renderBoard(savedDefinition());

    fireEvent.click(await screen.findByRole("button", { name: "Add notes to the board" }));

    expect(await screen.findByText("Not linked yet")).toBeInTheDocument();
    expect(latest.current?.source.relations).toHaveLength(1);
    expect(screen.getByText(/Now link “notes”/)).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Remove notes from the board"));
    await waitFor(() => expect(screen.queryByText("Not linked yet")).not.toBeInTheDocument());
  });

  it("renames a result column for the table view", async () => {
    stubCatalog();
    const latest = renderBoard(savedDefinition());

    fireEvent.click(await screen.findByRole("button", { name: "Id" }));
    fireEvent.change(screen.getByLabelText("Name in the table"), {
      target: { value: "Order number" },
    });

    await waitFor(() => expect(latest.current?.fields[0].display.label).toBe("Order number"));
    expect(latest.current?.fields[0].name).toBe("id");
  });
});
