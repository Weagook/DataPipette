/**
 * The API admits a small, installation-wide number of chart queries at once
 * (four by default) and answers the rest with 503 and Retry-After: 1. A page
 * that fired every chart query together would make its own extra charts wait a
 * second for a retry, so one page keeps at most this many chart queries in
 * flight and starts the next as soon as one finishes.
 */
export const MAX_CHART_QUERIES_IN_FLIGHT = 4;

let running = 0;
const waiting: Array<() => void> = [];

function release(): void {
  running -= 1;
  waiting.shift()?.();
}

export async function paceChartQuery<T>(task: () => Promise<T>, signal?: AbortSignal): Promise<T> {
  if (running >= MAX_CHART_QUERIES_IN_FLIGHT) {
    await new Promise<void>((resolve, reject) => {
      const start = () => {
        signal?.removeEventListener("abort", cancel);
        resolve();
      };
      const cancel = () => {
        const index = waiting.indexOf(start);
        if (index >= 0) waiting.splice(index, 1);
        reject(signal?.reason ?? new DOMException("Aborted", "AbortError"));
      };
      if (signal?.aborted) return cancel();
      signal?.addEventListener("abort", cancel, { once: true });
      waiting.push(start);
    });
  }
  running += 1;
  try {
    return await task();
  } finally {
    release();
  }
}
