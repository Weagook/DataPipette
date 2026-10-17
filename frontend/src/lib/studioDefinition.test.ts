import { describe, expect, it, vi } from "vitest";

import {
  appendJoinedRelation,
  appendVisualField,
  definitionIsReady,
  emptySqlDefinition,
  relationAlias,
  visualDefinitionFromRelation,
} from "./studioDefinition";

describe("dataset studio definition model", () => {
  it("creates stable editable visual fields from catalog metadata", () => {
    vi.spyOn(crypto, "randomUUID")
      .mockReturnValueOnce("00000000-0000-4000-8000-000000000001")
      .mockReturnValueOnce("00000000-0000-4000-8000-000000000002")
      .mockReturnValueOnce("00000000-0000-4000-8000-000000000003");
    const definition = visualDefinitionFromRelation({
      schema_name: "sales",
      name: "order_items",
      kind: "table",
      primary_key: ["id"],
      foreign_keys: [],
      columns: [
        {
          name: "id",
          ordinal_position: 1,
          data_type: "bigint",
          nullable: false,
        },
        {
          name: "net_amount",
          ordinal_position: 2,
          data_type: "numeric",
          nullable: false,
        },
      ],
    });

    expect(definition.source.relations[0]).toMatchObject({
      schema: "sales",
      name: "order_items",
      alias: "order_items",
    });
    expect(
      definition.fields.map((field) => [field.name, field.display.label]),
    ).toEqual([
      ["id", "Id"],
      ["net_amount", "Net amount"],
    ]);
  });

  it("orients an incoming foreign key as an explicit left join", () => {
    const base = {
      kind: "visual" as const,
      schema_version: 2 as const,
      source: {
        relations: [
          { id: "orders", schema: "public", name: "orders", alias: "orders" },
        ],
      },
      fields: [
        {
          id: "order-id",
          source: { relation_id: "orders", column: "id" },
          name: "id",
          display: { label: "Id", format: null, visible: true },
        },
      ],
      joins: [],
    };
    const result = appendJoinedRelation(
      base,
      {
        schema_name: "public",
        name: "customers",
        kind: "table",
        primary_key: ["id"],
        foreign_keys: [],
        columns: [
          {
            name: "id",
            ordinal_position: 1,
            data_type: "uuid",
            nullable: false,
          },
        ],
      },
      {
        foreign_key_name: "orders_customer_fk",
        source_relation_id: "orders",
        target_relation_id: "customers",
        conditions: [{ source_column: "customer_id", target_column: "id" }],
      },
      "customers",
    );

    expect(result.joins?.[0]).toMatchObject({
      left_relation_id: "orders",
      right_relation_id: "customers",
      conditions: [{ left_column: "customer_id", right_column: "id" }],
    });
    expect(result.fields.at(-1)?.name).toBe("id_2");
  });

  it("keeps generated aliases unique", () => {
    expect(relationAlias("Order Items", ["order_items"])).toBe("order_items_2");
  });

  it("lets an excluded visual column be selected again", () => {
    const definition = appendVisualField(
      {
        kind: "visual",
        schema_version: 2,
        source: {
          relations: [
            { id: "orders", schema: "public", name: "orders", alias: "orders" },
          ],
        },
        fields: [],
        joins: [],
      },
      "orders",
      "created_at",
    );

    expect(definition.fields).toHaveLength(1);
    expect(definition.fields[0]).toMatchObject({
      source: { relation_id: "orders", column: "created_at" },
      name: "created_at",
      display: { label: "Created at", visible: true },
    });
    expect(appendVisualField(definition, "orders", "created_at")).toBe(
      definition,
    );
  });

  it("requires a coherent editable definition before creation", () => {
    const sql = emptySqlDefinition();
    expect(definitionIsReady(sql)).toBe(false);
    expect(
      definitionIsReady({ ...sql, statement: "SELECT id FROM public.orders" }),
    ).toBe(true);
    expect(
      definitionIsReady({
        ...sql,
        statement: "SELECT id, name FROM public.orders",
        fields: [sql.fields[0], { ...sql.fields[0], id: "another-field" }],
      }),
    ).toBe(false);
  });
});
