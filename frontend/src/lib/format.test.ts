import { describe, expect, it } from "vitest";

import { formatDateTime, formatMetric, relativeTime } from "./format";

describe("localized formatting", () => {
  it("formats dates for the selected interface locale", () => {
    const value = "2026-10-18T12:47:00Z";

    expect(formatDateTime(value, "en")).toContain("Oct");
    expect(formatDateTime(value, "ru")).toContain("окт");
  });

  it("formats relative time for the selected interface locale", () => {
    const now = Date.parse("2026-10-26T12:47:00Z");

    expect(relativeTime("2026-10-19T12:47:00Z", "en", now)).toBe("last week");
    expect(relativeTime("2026-10-19T12:47:00Z", "ru", now)).toBe("на прошлой неделе");
  });

  it("formats numbers for the selected interface locale", () => {
    expect(formatMetric(1234.5, "en")).toBe("1,234.5");
    expect(formatMetric(1234.5, "ru")).toMatch(/^1[\u00a0\u202f ]234,5$/u);
  });
});
