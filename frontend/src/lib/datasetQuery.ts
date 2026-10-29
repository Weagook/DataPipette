import type { DatasetColumn, SnapshotFilter } from "./api";
import { randomUuid } from "./id";

export type FilterOperator = SnapshotFilter["operator"];

export interface FilterDraft {
  id: string;
  fieldId: string;
  operator: FilterOperator;
  value: string;
  secondValue: string;
}

export const operatorLabels: Record<FilterOperator, string> = {
  eq: "equals",
  neq: "does not equal",
  gt: "is greater than",
  gte: "is at least",
  lt: "is less than",
  lte: "is at most",
  between: "is between",
  in: "is any of",
  contains: "contains",
  starts_with: "starts with",
  is_null: "is empty",
  is_not_null: "is not empty",
};

export function newFilterDraft(column: DatasetColumn): FilterDraft {
  return {
    id: randomUuid(),
    fieldId: column.field_id,
    operator: column.filter_operators[0] ?? "eq",
    value: "",
    secondValue: "",
  };
}

export function buildSnapshotFilters(
  drafts: FilterDraft[],
  columns: DatasetColumn[],
): SnapshotFilter[] {
  const byId = new Map(columns.map((column) => [column.field_id, column]));
  return drafts.map((draft) => {
    const column = byId.get(draft.fieldId);
    if (!column || !column.filter_operators.includes(draft.operator)) {
      throw new Error("Choose a valid column and operator for every filter.");
    }
    if (draft.operator === "is_null" || draft.operator === "is_not_null") {
      return { field_id: draft.fieldId, operator: draft.operator };
    }
    if (draft.operator === "in") {
      const values = draft.value
        .split(";")
        .map((value) => value.trim())
        .filter(Boolean);
      if (values.length === 0) throw new Error(`${column.label} needs at least one value.`);
      if (values.length > 100) throw new Error(`${column.label} accepts at most 100 values.`);
      return {
        field_id: draft.fieldId,
        operator: draft.operator,
        values: values.map((value) => parseFilterValue(column, value)),
      };
    }
    if (draft.operator === "between") {
      if (!draft.value.trim() || !draft.secondValue.trim()) {
        throw new Error(`${column.label} needs both ends of the range.`);
      }
      return {
        field_id: draft.fieldId,
        operator: draft.operator,
        values: [
          parseFilterValue(column, draft.value),
          parseFilterValue(column, draft.secondValue),
        ],
      };
    }
    if (!draft.value.trim()) throw new Error(`${column.label} needs a value.`);
    return {
      field_id: draft.fieldId,
      operator: draft.operator,
      value: parseFilterValue(column, draft.value),
    };
  });
}

export function inputType(column: DatasetColumn): "date" | "datetime-local" | "number" | "text" {
  const type = normalizeType(column.data_type);
  if (type === "date") return "date";
  if (type.startsWith("timestamp")) return "datetime-local";
  if (isInteger(type) || isBoundedNumber(type)) return "number";
  return "text";
}

export function valuePlaceholder(column: DatasetColumn, operator: FilterOperator): string {
  if (operator === "in") return "North; South; Enterprise";
  if (normalizeType(column.data_type) === "boolean") return "true or false";
  if (normalizeType(column.data_type).includes("time") && !normalizeType(column.data_type).startsWith("timestamp")) return "09:30:00";
  if (normalizeType(column.data_type) === "json" || normalizeType(column.data_type) === "jsonb") return '{"status":"active"}';
  return `Enter ${column.label.toLocaleLowerCase()}`;
}

export function formatDatasetValue(
  value: unknown,
  column: DatasetColumn,
  installationTimezone = "UTC",
  locale: "en" | "ru" = "en",
): string {
  if (value == null) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  const format = column.display_format;
  if (format === "date_dd_mm_yyyy" || format === "date_yyyy_mm_dd") {
    const [year, month, day] = String(value).slice(0, 10).split("-");
    if (year && month && day) return format === "date_dd_mm_yyyy" ? `${day}-${month}-${year}` : `${year}-${month}-${day}`;
  }
  if (format === "datetime_dd_mm_yyyy_hh_mm") {
    const type = normalizeType(column.data_type);
    if (type.includes("without time zone")) {
      const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/);
      if (match) return `${match[3]}-${match[2]}-${match[1]} ${match[4]}:${match[5]}`;
    }
    const parsed = new Date(String(value));
    if (!Number.isNaN(parsed.getTime())) {
      const parts = new Intl.DateTimeFormat("en-GB", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
        timeZone: installationTimezone,
      }).formatToParts(parsed);
      const part = (type: Intl.DateTimeFormatPartTypes) =>
        parts.find((candidate) => candidate.type === type)?.value ?? "";
      return `${part("day")}-${part("month")}-${part("year")} ${part("hour")}:${part("minute")}`;
    }
  }
  if (format === "number_integer") {
    return formatExactDecimal(value, 0, 0, locale);
  }
  if (format === "number_decimal_2") {
    return formatExactDecimal(value, 2, 0, locale);
  }
  if (format === "percent") {
    return `${formatExactDecimal(value, 2, 2, locale)}%`;
  }
  return formatByType(value, normalizeType(column.data_type), installationTimezone, locale);
}

/** Readable defaults for columns without an explicit display format. */
function formatByType(
  value: unknown,
  type: string,
  installationTimezone: string,
  locale: "en" | "ru",
): string {
  const tag = locale === "ru" ? "ru-RU" : "en-GB";
  if (typeof value === "boolean") return locale === "ru" ? (value ? "Да" : "Нет") : value ? "Yes" : "No";
  if (isInteger(type) || isBoundedNumber(type)) {
    const raw = String(value).trim();
    const fraction = raw.match(/^[+-]?\d+\.(\d+)$/)?.[1]?.length ?? 0;
    return /^[+-]?\d+(\.\d+)?$/.test(raw) ? formatExactDecimal(raw, fraction, 0, locale) : raw;
  }
  if (type === "date") {
    const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (match) return new Intl.DateTimeFormat(tag, { dateStyle: "medium", timeZone: "UTC" }).format(new Date(`${match[0]}T00:00:00Z`));
  }
  if (/^timestamp/.test(type)) {
    const local = type.includes("without time zone");
    const parsed = new Date(local ? `${String(value).replace(" ", "T").slice(0, 19)}Z` : String(value));
    if (!Number.isNaN(parsed.getTime()))
      return new Intl.DateTimeFormat(tag, {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: local ? "UTC" : installationTimezone,
      }).format(parsed);
  }
  return String(value);
}

function parseFilterValue(column: DatasetColumn, raw: string): unknown {
  const value = raw.trim();
  const type = normalizeType(column.data_type);
  if (type === "boolean") {
    if (value.toLowerCase() === "true") return true;
    if (value.toLowerCase() === "false") return false;
    throw new Error(`${column.label} accepts only true or false.`);
  }
  if (isInteger(type)) {
    const parsed = Number(value);
    if (!Number.isSafeInteger(parsed)) throw new Error(`${column.label} needs a whole number.`);
    return parsed;
  }
  if (isBoundedNumber(type)) {
    if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(value)) {
      throw new Error(`${column.label} needs a valid number.`);
    }
    return value;
  }
  if (type === "json" || type === "jsonb") {
    try {
      return JSON.parse(value) as unknown;
    } catch {
      throw new Error(`${column.label} needs valid JSON.`);
    }
  }
  return value;
}

function normalizeType(value: string): string {
  return value.toLocaleLowerCase().replace(/\s+/g, " ").trim();
}

function isInteger(value: string): boolean {
  return value === "smallint" || value === "integer" || value === "bigint";
}

function isBoundedNumber(value: string): boolean {
  return value === "real" || value === "double precision" || /^(numeric|decimal)\(\d+,-?\d+\)$/.test(value);
}

function formatExactDecimal(
  value: unknown,
  fractionDigits: number,
  power = 0,
  locale: "en" | "ru" = "en",
): string {
  const raw = String(value).trim();
  const match = raw.match(/^([+-]?)(\d+)(?:\.(\d+))?$/);
  const [group, point] = locale === "ru" ? ["\u00a0", ","] : [",", "."];
  if (!match) {
    return new Intl.NumberFormat(locale === "ru" ? "ru-RU" : "en-US", {
      minimumFractionDigits: fractionDigits,
      maximumFractionDigits: fractionDigits,
    }).format(Number(value));
  }
  const sign = match[1] === "-" ? "-" : "";
  const fraction = match[3] ?? "";
  let scaled = BigInt(`${match[2]}${fraction}`);
  const exponent = fractionDigits + power - fraction.length;
  if (exponent >= 0) {
    scaled *= 10n ** BigInt(exponent);
  } else {
    const divisor = 10n ** BigInt(-exponent);
    const remainder = scaled % divisor;
    scaled /= divisor;
    if (remainder * 2n >= divisor) scaled += 1n;
  }
  const base = 10n ** BigInt(fractionDigits);
  const integer = (scaled / base).toString().replace(/\B(?=(\d{3})+(?!\d))/g, group);
  if (fractionDigits === 0) return `${sign}${integer}`;
  const decimals = (scaled % base).toString().padStart(fractionDigits, "0");
  return `${sign}${integer}${point}${decimals}`;
}
