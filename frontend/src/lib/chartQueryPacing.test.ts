import { describe, expect, it } from "vitest";

import { MAX_CHART_QUERIES_IN_FLIGHT, paceChartQuery } from "./chartQueryPacing";

function deferred() {
  let resolve!: (value: string) => void;
  const promise = new Promise<string>((done) => (resolve = done));
  return { promise, resolve };
}

describe("chart query pacing", () => {
  it("keeps at most four queries in flight and starts the next when one finishes", async () => {
    const tasks = Array.from({ length: 6 }, () => deferred());
    let started = 0;
    const results = tasks.map((task, index) =>
      paceChartQuery(() => {
        started += 1;
        return task.promise.then((value) => `${index}:${value}`);
      }),
    );
    await Promise.resolve();
    expect(started).toBe(MAX_CHART_QUERIES_IN_FLIGHT);
    tasks[0].resolve("a");
    await results[0];
    await Promise.resolve();
    expect(started).toBe(5);
    tasks.slice(1).forEach((task) => task.resolve("b"));
    await expect(Promise.all(results)).resolves.toHaveLength(6);
    expect(started).toBe(6);
  });

  it("drops a waiting query that is cancelled before it starts", async () => {
    const blockers = Array.from({ length: MAX_CHART_QUERIES_IN_FLIGHT }, () => deferred());
    const running = blockers.map((blocker) => paceChartQuery(() => blocker.promise));
    const controller = new AbortController();
    let ran = false;
    const waiting = paceChartQuery(async () => {
      ran = true;
      return "late";
    }, controller.signal);
    controller.abort();
    await expect(waiting).rejects.toBeDefined();
    blockers.forEach((blocker) => blocker.resolve("done"));
    await Promise.all(running);
    expect(ran).toBe(false);
    await expect(paceChartQuery(async () => "free")).resolves.toBe("free");
  });
});
