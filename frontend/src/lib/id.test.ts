import { describe, expect, it } from "vitest";

import { randomUuid } from "./id";

describe("randomUuid", () => {
  it("uses getRandomValues when randomUUID is unavailable", () => {
    const source = {
      getRandomValues: <T extends ArrayBufferView | null>(value: T): T => {
        const bytes = value as Uint8Array;
        bytes.forEach((_, index) => {
          bytes[index] = index;
        });
        return value;
      },
    };

    expect(randomUuid(source)).toBe("00010203-0405-4607-8809-0a0b0c0d0e0f");
  });
});
