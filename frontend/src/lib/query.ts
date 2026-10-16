import { QueryClient } from "@tanstack/react-query";
import { DatePipetteApiError } from "@datepipette/api-client";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 10 * 60_000,
      refetchOnWindowFocus: false,
      retry(failureCount, error) {
        const status =
          typeof error === "object" && error !== null && "status" in error
            ? Number(error.status)
            : 0;
        return status >= 500 && failureCount < 2;
      },
      retryDelay(attempt, error) {
        if (error instanceof DatePipetteApiError && error.retryAfterSeconds !== null) {
          return error.retryAfterSeconds * 1_000;
        }
        return Math.min(500 * 2 ** attempt, 3_000);
      },
    },
    mutations: { retry: false },
  },
});

export const queryKeys = {
  session: ["session"] as const,
  dashboardOverview: ["dashboards", "overview"] as const,
  dashboards: ["dashboards", "infinite"] as const,
  dashboard: (id: string) => ["dashboard", id] as const,
  dashboardItem: (dashboardId: string, itemId: string) =>
    ["dashboard", dashboardId, "item", itemId] as const,
  datasets: ["datasets", "infinite"] as const,
  datasetExploration: (id: string) => ["dataset", id, "exploration"] as const,
  datasetRows: (id: string, query: unknown) => ["dataset", id, "rows", query] as const,
  datasetExports: (id: string) => ["dataset", id, "exports"] as const,
};
