import { describe, expect, it } from "vitest";

import type { CatalogRelation } from "./api";
import {
  addJoinCondition,
  attachTable,
  autoLayoutCanvas,
  canvasNodeId,
  catalogTypesCompatible,
  definitionFromMainTable,
  detachTable,
  directJoin,
  ensureCanvas,
  friendlyType,
  removeJoinCondition,
  setJoinType,
  suggestionLink,
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

const CUSTOMERS: CatalogRelation = {
  schema_name: "crm",
  name: "customers",
  kind: "table",
  primary_key: ["id"],
  foreign_keys: [],
  columns: [
    { name: "id", data_type: "bigint", nullable: false, ordinal_position: 1 },
    { name: "name", data_type: "text", nullable: true, ordinal_position: 2 },
  ],
};

function rootOnly(): VisualDefinition {
  return {
    ...definition(),
    source: { relations: definition().source.relations.slice(0, 1) },
    fields: definition().fields.slice(0, 1),
    joins: [],
  };
}

describe("canvas definition helpers", () => {
  it("lays tables out by link depth and keeps saved positions", () => {
    const laidOut = autoLayoutCanvas(definition());
    expect(laidOut.canvas?.nodes).toEqual([
      { id: canvasNodeId.relation(ROOT_ID), x: 40, y: 40 },
      { id: canvasNodeId.relation(CHILD_ID), x: 460, y: 40 },
      { id: canvasNodeId.relation(GRANDCHILD_ID), x: 880, y: 40 },
    ]);
    expect(laidOut.canvas?.viewport).toEqual({ x: 0, y: 0, zoom: 0.9 });

    const moved = updateCanvasNodePositions(laidOut, [
      { id: canvasNodeId.relation(ROOT_ID), x: 432, y: 210 },
    ]);
    expect(
      ensureCanvas(moved).canvas?.nodes?.find(
        (node) => node.id === canvasNodeId.relation(ROOT_ID),
      ),
    ).toMatchObject({ x: 432, y: 210 });
  });

  it("drops layout entries of the former pipeline nodes", () => {
    const legacy: VisualDefinition = {
      ...definition(),
      canvas: {
        nodes: [
          { id: canvasNodeId.relation(ROOT_ID), x: 5, y: 6 },
          { id: "select", x: 0, y: 0 },
          { id: "output", x: 0, y: 0 },
        ],
        viewport: { x: 1, y: 2, zoom: 1 },
      },
    };

    const reopened = ensureCanvas(legacy);

    expect(reopened.canvas?.nodes?.map((node) => node.id)).toEqual([
      canvasNodeId.relation(ROOT_ID),
      canvasNodeId.relation(CHILD_ID),
      canvasNodeId.relation(GRANDCHILD_ID),
    ]);
    expect(reopened.canvas?.nodes?.[0]).toMatchObject({ x: 5, y: 6 });
    expect(reopened.canvas?.viewport).toEqual({ x: 1, y: 2, zoom: 1 });
  });

  it("starts from a main table with every column selected", () => {
    const started = definitionFromMainTable(CUSTOMERS, { x: 12, y: 34 });
    const relationId = started.source.relations[0].id;

    expect(started.fields.map((field) => field.source.column)).toEqual(["id", "name"]);
    expect(started.canvas?.nodes).toEqual([
      { id: canvasNodeId.relation(relationId), x: 12, y: 34 },
    ]);
  });

  it("links a new table without selecting its columns", () => {
    const linked = attachTable(
      rootOnly(),
      { schema: "crm", name: "orders" },
      CHILD_ID,
      ROOT_ID,
      [{ left_column: "customer_id", right_column: "id" }],
      { x: 400, y: 80 },
    );

    expect(linked.source.relations[1]).toMatchObject({
      id: CHILD_ID,
      alias: "orders_2",
    });
    expect(linked.fields).toHaveLength(1);
    expect(linked.joins?.[0]).toMatchObject({
      type: "left",
      left_relation_id: ROOT_ID,
      right_relation_id: CHILD_ID,
      conditions: [{ left_column: "customer_id", right_column: "id" }],
    });
    expect(linked.canvas?.nodes?.at(-1)).toEqual({
      id: canvasNodeId.relation(CHILD_ID),
      x: 400,
      y: 80,
    });
  });

  it("orients foreign-key suggestions so the new table is the joined side", () => {
    const candidate = "00000000-0000-4000-8000-000000000099";
    const conditions = [{ source_column: "customer_id", target_column: "id" }];

    expect(
      suggestionLink(
        rootOnly(),
        {
          foreign_key_name: "orders_customer",
          source_relation_id: ROOT_ID,
          target_relation_id: candidate,
          conditions,
        },
        candidate,
      ),
    ).toEqual({
      linkedRelationId: ROOT_ID,
      conditions: [{ left_column: "customer_id", right_column: "id" }],
    });
    expect(
      suggestionLink(
        rootOnly(),
        {
          foreign_key_name: "lines_order",
          source_relation_id: candidate,
          target_relation_id: ROOT_ID,
          conditions,
        },
        candidate,
      ),
    ).toEqual({
      linkedRelationId: ROOT_ID,
      conditions: [{ left_column: "id", right_column: "customer_id" }],
    });
    expect(
      suggestionLink(
        rootOnly(),
        {
          foreign_key_name: "unrelated",
          source_relation_id: CHILD_ID,
          target_relation_id: GRANDCHILD_ID,
          conditions,
        },
        candidate,
      ),
    ).toBeNull();
  });

  it("adds, changes, and removes matching columns of one link", () => {
    const base = definition();
    expect(directJoin(base, CHILD_ID, ROOT_ID)?.id).toBe(JOIN_ID);
    expect(directJoin(base, ROOT_ID, GRANDCHILD_ID)).toBeUndefined();

    const twoColumns = addJoinCondition(
      addJoinCondition(base, JOIN_ID, { left_column: "region", right_column: "region" }),
      JOIN_ID,
      { left_column: "region", right_column: "region" },
    );
    expect(twoColumns.joins?.[0].conditions).toHaveLength(2);
    expect(setJoinType(twoColumns, JOIN_ID, "inner").joins?.[0].type).toBe("inner");

    const oneColumn = removeJoinCondition(twoColumns, JOIN_ID, 0);
    expect(oneColumn.removed).toEqual([]);
    expect(oneColumn.definition.joins?.[0].conditions).toEqual([
      { left_column: "region", right_column: "region" },
    ]);
  });

  it("unlinks a table with everything linked through it and remembers positions", () => {
    const { definition: next, removed } = detachTable(
      autoLayoutCanvas(definition()),
      CHILD_ID,
    );

    expect(next.source.relations.map((relation) => relation.id)).toEqual([ROOT_ID]);
    expect(next.fields.map((field) => field.name)).toEqual(["order_id"]);
    expect(next.joins).toEqual([]);
    expect(next.canvas?.nodes?.map((node) => node.id)).toEqual([
      canvasNodeId.relation(ROOT_ID),
    ]);
    expect(removed.map((item) => [item.relation.id, item.position])).toEqual([
      [CHILD_ID, { x: 460, y: 40 }],
      [GRANDCHILD_ID, { x: 880, y: 40 }],
    ]);
    expect(removeJoinCondition(definition(), CHILD_JOIN_ID, 0).removed).toHaveLength(1);
  });

  it("limits links to broadly compatible column types and names them plainly", () => {
    expect(catalogTypesCompatible("bigint", "numeric(12,2)")).toBe(true);
    expect(catalogTypesCompatible("uuid", "uuid")).toBe(true);
    expect(catalogTypesCompatible("uuid", "text")).toBe(false);
    expect(catalogTypesCompatible("timestamp with time zone", "date")).toBe(true);
    expect(catalogTypesCompatible("bigint", "date")).toBe(false);
    expect(friendlyType("numeric(12,2)")).toBe("number");
    expect(friendlyType("character varying(20)")).toBe("text");
    expect(friendlyType("timestamp with time zone")).toBe("date");
    expect(friendlyType("boolean")).toBe("yes/no");
    expect(friendlyType("uuid")).toBe("id");
    expect(friendlyType("jsonb")).toBe("other");
  });
});
