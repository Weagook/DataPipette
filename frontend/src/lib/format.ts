import type { InterfaceLocale } from "./i18n";

function localeTag(locale: InterfaceLocale): string {
  return locale === "ru" ? "ru-RU" : "en-US";
}

export function formatDateTime(value: string, locale: InterfaceLocale = "en"): string {
  return new Intl.DateTimeFormat(localeTag(locale), {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function relativeTime(
  value: string,
  locale: InterfaceLocale = "en",
  now = Date.now(),
): string {
  const seconds = Math.round((new Date(value).getTime() - now) / 1000);
  const ranges: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["year", 31_536_000],
    ["month", 2_592_000],
    ["week", 604_800],
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
  ];
  const formatter = new Intl.RelativeTimeFormat(localeTag(locale), { numeric: "auto" });
  for (const [unit, size] of ranges) {
    if (Math.abs(seconds) >= size) {
      return formatter.format(Math.round(seconds / size), unit);
    }
  }
  return formatter.format(seconds, "second");
}

export function formatMetric(value: unknown, locale: InterfaceLocale = "en"): string {
  if (typeof value === "number") {
    return new Intl.NumberFormat(localeTag(locale), { maximumFractionDigits: 2 }).format(value);
  }
  if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) {
    return new Intl.NumberFormat(localeTag(locale), { maximumFractionDigits: 2 }).format(Number(value));
  }
  return value == null ? "—" : String(value);
}
