import type { DashboardDefinition, DashboardItem } from "./api";

export const DASHBOARD_COLUMNS = 12;
export const MAX_DASHBOARD_ITEMS = 50;
export const MAX_DASHBOARD_ROW = 10_000;
export const MAX_DASHBOARD_ITEM_HEIGHT = 100;
export const DEFAULT_ITEM_WIDTH = 6;
export const DEFAULT_ITEM_HEIGHT = 4;

export function emptyDashboardDefinition(): DashboardDefinition {
  return { schema_version: 2, items: [], filters: [] };
}

// Layout edits keep the dashboard filters; version 2 is the document that carries them.
function withItems(definition: DashboardDefinition, items: DashboardItem[]): DashboardDefinition {
  return { schema_version: 2, items, filters: definition.filters ?? [] };
}

export function itemsOf(definition: DashboardDefinition): DashboardItem[] {
  return definition.items ?? [];
}

export function overlaps(left: DashboardItem, right: DashboardItem): boolean {
  return (
    left.x < right.x + right.width &&
    right.x < left.x + left.width &&
    left.y < right.y + right.height &&
    right.y < left.y + left.height
  );
}

export function isValidLayout(items: DashboardItem[]): boolean {
  const itemIds = new Set(items.map((item) => item.id));
  const chartIds = new Set(items.map((item) => item.chart_id));
  return (
    items.length <= MAX_DASHBOARD_ITEMS &&
    itemIds.size === items.length &&
    chartIds.size === items.length &&
    items.every(
    (item, index) =>
      Number.isInteger(item.x) &&
      Number.isInteger(item.y) &&
      Number.isInteger(item.width) &&
      Number.isInteger(item.height) &&
      item.x >= 0 &&
      item.y >= 0 &&
      item.y < MAX_DASHBOARD_ROW &&
      item.width >= 1 &&
      item.height >= 1 &&
      item.height <= MAX_DASHBOARD_ITEM_HEIGHT &&
      item.x + item.width <= DASHBOARD_COLUMNS &&
      !items.slice(index + 1).some((other) => overlaps(item, other)),
    )
  );
}

export function firstAvailablePosition(
  items: DashboardItem[],
  width = DEFAULT_ITEM_WIDTH,
  height = DEFAULT_ITEM_HEIGHT,
): Pick<DashboardItem, "x" | "y" | "width" | "height"> {
  for (let y = 0; y < 10_000; y += 1) {
    for (let x = 0; x <= DASHBOARD_COLUMNS - width; x += 1) {
      const candidate = {
        id: "candidate",
        chart_id: "candidate",
        x,
        y,
        width,
        height,
      };
      if (!items.some((item) => overlaps(candidate, item))) return { x, y, width, height };
    }
  }
  throw new Error("The dashboard has no available layout position.");
}

export function addChart(
  definition: DashboardDefinition,
  chartId: string,
  itemId: string,
): DashboardDefinition {
  const items = itemsOf(definition);
  if (items.some((item) => item.chart_id === chartId)) return definition;
  return withItems(definition, [
    ...items,
    { id: itemId, chart_id: chartId, ...firstAvailablePosition(items) },
  ]);
}

export function updateItem(
  definition: DashboardDefinition,
  itemId: string,
  change: Partial<Pick<DashboardItem, "x" | "y" | "width" | "height">>,
): DashboardDefinition | null {
  const next = itemsOf(definition).map((item) =>
    item.id === itemId ? { ...item, ...change } : item,
  );
  return isValidLayout(next) ? withItems(definition, next) : null;
}

export function removeItem(
  definition: DashboardDefinition,
  itemId: string,
): DashboardDefinition {
  const filters = (definition.filters ?? [])
    .map((filter) => ({ ...filter, targets: filter.targets.filter((target) => target.item_id !== itemId) }))
    .filter((filter) => filter.targets.length > 0);
  return {
    ...withItems(definition, itemsOf(definition).filter((item) => item.id !== itemId)),
    filters,
  };
}

export function compactLayout(definition: DashboardDefinition): DashboardDefinition {
  const compacted: DashboardItem[] = [];
  for (const item of itemsOf(definition)) {
    let candidate = { ...item, y: 0 };
    while (compacted.some((other) => overlaps(candidate, other))) {
      candidate = { ...candidate, y: candidate.y + 1 };
    }
    compacted.push(candidate);
  }
  return withItems(definition, compacted);
}
