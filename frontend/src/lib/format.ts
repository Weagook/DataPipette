const PRODUCT_LOCALE = "en-US";

export function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat(PRODUCT_LOCALE, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function relativeTime(value: string, now = Date.now()): string {
  const seconds = Math.round((new Date(value).getTime() - now) / 1000);
  const ranges: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["year", 31_536_000],
    ["month", 2_592_000],
    ["week", 604_800],
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
  ];
  const formatter = new Intl.RelativeTimeFormat(PRODUCT_LOCALE, { numeric: "auto" });
  for (const [unit, size] of ranges) {
    if (Math.abs(seconds) >= size) {
      return formatter.format(Math.round(seconds / size), unit);
    }
  }
  return formatter.format(seconds, "second");
}

export function formatMetric(value: unknown): string {
  if (typeof value === "number") {
    return new Intl.NumberFormat(PRODUCT_LOCALE, { maximumFractionDigits: 2 }).format(value);
  }
  if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) {
    return new Intl.NumberFormat(PRODUCT_LOCALE, { maximumFractionDigits: 2 }).format(Number(value));
  }
  return value == null ? "—" : String(value);
}
