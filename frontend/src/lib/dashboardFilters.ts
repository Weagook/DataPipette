import type { ApiSchemas } from "./api";

export type DashboardFilter = ApiSchemas["DashboardFilterPayload"];
export type DashboardFilterKind = DashboardFilter["kind"];
export type FilterSelection = ApiSchemas["DashboardFilterSelectionPayload"];
export type FilterValue = NonNullable<FilterSelection["values"]>[number];

export const MAX_DASHBOARD_FILTERS = 5;
const SELECTION_PARAM = "filters";

/** Which dashboard filter a dataset column can drive, mirroring the backend type families. */
export function filterKindFor(dataType: string): DashboardFilterKind | null {
  const type = dataType.toLowerCase().trim().replace(/\s+/g, " ");
  if (type === "date" || type.startsWith("timestamp")) return "date_range";
  if (
    type === "boolean" ||
    ["smallint", "integer", "bigint", "text", "citext", "uuid", "name"].includes(type) ||
    type.startsWith("character") ||
    type.startsWith("varchar") ||
    type.startsWith("char")
  )
    return "values";
  return null;
}

export function isActive(selection: FilterSelection): boolean {
  return Boolean(selection.values?.length || selection.from || selection.to);
}

/** Read the viewer's choices from the page address, keeping only filters the dashboard still has. */
export function readSelections(params: URLSearchParams, filters: DashboardFilter[]): FilterSelection[] {
  const raw = params.get(SELECTION_PARAM);
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const known = new Map(filters.map((filter) => [filter.id, filter.kind]));
    return parsed.flatMap((item): FilterSelection[] => {
      if (typeof item !== "object" || item === null) return [];
      const value = item as Record<string, unknown>;
      const kind = known.get(String(value.filter_id));
      if (kind === "values" && Array.isArray(value.values)) {
        const values = value.values.filter((entry): entry is FilterValue => ["string", "number", "boolean"].includes(typeof entry));
        return values.length ? [{ filter_id: String(value.filter_id), values }] : [];
      }
      if (kind === "date_range") {
        const from = isDay(value.from) ? value.from : null;
        const to = isDay(value.to) ? value.to : null;
        return from || to ? [{ filter_id: String(value.filter_id), from, to }] : [];
      }
      return [];
    });
  } catch {
    return [];
  }
}

export function writeSelections(params: URLSearchParams, selections: FilterSelection[]): URLSearchParams {
  const next = new URLSearchParams(params);
  const active = selections.filter(isActive);
  if (active.length) next.set(SELECTION_PARAM, JSON.stringify(active));
  else next.delete(SELECTION_PARAM);
  return next;
}

/** Only the choices whose filter targets this item, so unrelated charts keep their cached result. */
export function selectionsForItem(itemId: string, filters: DashboardFilter[], selections: FilterSelection[]): FilterSelection[] {
  const targeted = new Set(filters.filter((filter) => filter.targets.some((target) => target.item_id === itemId)).map((filter) => filter.id));
  return selections.filter((selection) => targeted.has(selection.filter_id) && isActive(selection));
}

function isDay(value: unknown): value is string {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
}
