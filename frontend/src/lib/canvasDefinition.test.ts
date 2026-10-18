import { describe, expect, it } from "vitest";

import type { CatalogRelation } from "./api";
import {
  appendManualJoinedRelation,
  autoLayoutCanvas,
  canvasNodeId,
  catalogTypesCompatible,
  ensureCanvas,
  removeJoinSubtree,
  updateCanvasNodePositions,
} from "./canvasDefinition";
import type { VisualDefinition } from "./studioDefinition";

const ROOT_ID = "00000000-0000-4000-8000-000000000001";
const CHILD_ID = "00000000-0000-4000-8000-000000000002";
const GRANDCHILD_ID = "00000000-0000-4000-8000-000000000003";
const JOIN_ID = "00000000-0000-4000-8000-000000000011";
const CHILD_JOIN_ID = "00000000-0000-4000-8000-000000000012";

function definition(): VisualDefinition {
  return {
    kind: "visual",
    schema_version: 2,
    source: {
      relations: [
        { id: ROOT_ID, schema: "sales", name: "orders", alias: "orders" },
        { id: CHILD_ID, schema: "crm", name: "customers", alias: "customers" },
        { id: GRANDCHILD_ID, schema: "crm", name: "regions", alias: "regions" },
      ],
    },
    fields: [
      {
        id: "00000000-0000-4000-8000-000000000021",
        source: { relation_id: ROOT_ID, column: "id" },
        name: "order_id",
        display: { label: "Order ID", format: null, visible: true },
      },
      {
        id: "00000000-0000-4000-8000-000000000022",
        source: { relation_id: CHILD_ID, column: "name" },
        name: "customer_name",
        display: { label: "Customer", format: null, visible: true },
      },
      {
        id: "00000000-0000-4000-8000-000000000023",
        source: { relation_id: GRANDCHILD_ID, column: "name" },
        name: "region_name",
        display: { label: "Region", format: null, visible: true },
      },
    ],
    joins: [
      {
        id: JOIN_ID,
        type: "left",
        left_relation_id: ROOT_ID,
        right_relation_id: CHILD_ID,
        conditions: [{ left_column: "customer_id", right_column: "id" }],
      },
      {
        id: CHILD_JOIN_ID,
        type: "left",
        left_relation_id: CHILD_ID,
        right_relation_id: GRANDCHILD_ID,
        conditions: [{ left_column: "region_id", right_column: "id" }],
      },
    ],
  };
}

describe("canvas definition helpers", () => {
  it("creates and preserves deterministic bounded layout metadata", () => {
    const laidOut = autoLayoutCanvas(definition());
    expect(laidOut.canvas?.nodes).toHaveLength(8);
    expect(laidOut.canvas?.viewport).toEqual({ x: 0, y: 0, zoom: 0.85 });

    const moved = updateCanvasNodePositions(laidOut, [
      { id: canvasNodeId.relation(ROOT_ID), x: 432, y: 210 },
    ]);
    const reopened = ensureCanvas(moved);
    expect(
      reopened.canvas?.nodes?.find(
        (node) => node.id === canvasNodeId.relation(ROOT_ID),
      ),
    ).toMatchObject({ x: 432, y: 210 });
  });

  it("deletes the selected join subtree while preserving the rooted graph", () => {
    const next = removeJoinSubtree(autoLayoutCanvas(definition()), JOIN_ID);

    expect(next.source.relations.map((relation) => relation.id)).toEqual([
      ROOT_ID,
    ]);
    expect(next.fields.map((field) => field.name)).toEqual(["order_id"]);
    expect(next.joins).toEqual([]);
    expect(next.canvas?.nodes?.map((node) => node.id)).toEqual([
      canvasNodeId.relation(ROOT_ID),
      canvasNodeId.select,
      canvasNodeId.format,
      canvasNodeId.output,
    ]);
  });

  it("adds a manual compatible-column join to the same visual contract", () => {
    const rootOnly: VisualDefinition = {
      ...definition(),
      source: { relations: definition().source.relations.slice(0, 1) },
      fields: definition().fields.slice(0, 1),
      joins: [],
    };
    const relation: CatalogRelation = {
      schema_name: "crm",
      name: "customers",
      kind: "table",
      primary_key: ["id"],
      foreign_keys: [],
      columns: [
        {
          name: "id",
          data_type: "bigint",
          nullable: false,
          ordinal_position: 1,
        },
      ],
    };

    const joined = appendManualJoinedRelation(
      rootOnly,
      relation,
      ROOT_ID,
      "customer_id",
      "id",
    );

    expect(joined.source.relations).toHaveLength(2);
    expect(joined.joins?.[0]).toMatchObject({
      left_relation_id: ROOT_ID,
      type: "left",
      conditions: [{ left_column: "customer_id", right_column: "id" }],
    });
    expect(joined.canvas?.nodes).toHaveLength(6);
  });

  it("limits manual choices to broad compatible PostgreSQL type families", () => {
    expect(catalogTypesCompatible("bigint", "numeric(12,2)")).toBe(true);
    expect(catalogTypesCompatible("uuid", "uuid")).toBe(true);
    expect(catalogTypesCompatible("uuid", "text")).toBe(false);
    expect(catalogTypesCompatible("timestamp with time zone", "date")).toBe(
      true,
    );
    expect(catalogTypesCompatible("bigint", "date")).toBe(false);
  });
});
