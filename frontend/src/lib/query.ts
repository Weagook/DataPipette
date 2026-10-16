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
        if (
          error instanceof DatePipetteApiError &&
          error.retryAfterSeconds !== null
        ) {
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
  dashboardEdit: (id: string) => ["dashboard", id, "edit"] as const,
  dashboardCharts: ["dashboards", "charts"] as const,
  dashboardGrants: (id: string) => ["dashboard", id, "grants"] as const,
  users: ["access", "users"] as const,
  groups: ["access", "groups"] as const,
  dashboardItem: (dashboardId: string, itemId: string) =>
    ["dashboard", dashboardId, "item", itemId] as const,
  datasets: ["datasets", "infinite"] as const,
  connections: ["connections"] as const,
  catalogSchemas: (connectionId: string) =>
    ["catalog", connectionId, "schemas"] as const,
  catalogRelations: (connectionId: string, schema: string) =>
    ["catalog", connectionId, schema, "relations"] as const,
  catalogRelation: (connectionId: string, schema: string, relation: string) =>
    ["catalog", connectionId, schema, relation] as const,
  dataset: (id: string) => ["dataset", id] as const,
  datasetDraft: (id: string) => ["dataset", id, "draft"] as const,
  refreshRun: (datasetId: string, runId: string) =>
    ["dataset", datasetId, "refresh", runId] as const,
  refreshSchedule: (id: string) => ["dataset", id, "schedule"] as const,
  datasetExploration: (id: string) => ["dataset", id, "exploration"] as const,
  datasetRows: (id: string, query: unknown) =>
    ["dataset", id, "rows", query] as const,
  datasetExports: (id: string) => ["dataset", id, "exports"] as const,
  charts: ["charts", "infinite"] as const,
  chartDatasets: ["charts", "datasets"] as const,
  chart: (id: string) => ["chart", id] as const,
  chartPreview: (id: string, revision: number) =>
    ["chart", id, "preview", revision] as const,
};
