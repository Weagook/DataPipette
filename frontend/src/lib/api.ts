import {
  createBrowserClient,
  DatePipetteApiError,
  unwrap,
  type components,
} from "@datepipette/api-client";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim() || window.location.origin;

export const api = createBrowserClient({
  baseUrl: apiBaseUrl,
  fetch: (input, init) => globalThis.fetch(input, init),
});

export type ApiSchemas = components["schemas"];
export type CurrentUser = ApiSchemas["UserResponse"];
export type Dashboard = ApiSchemas["DashboardResponse"];
export type DashboardPage = ApiSchemas["DashboardCollectionResponse"];
export type DashboardItemResult = ApiSchemas["DashboardItemQueryResponse"];
export type Dataset = ApiSchemas["DatasetResponse"];
export type DatasetPage = ApiSchemas["DatasetCollectionResponse"];
export type DatasetExploration = ApiSchemas["DatasetExplorationResponse"];
export type DatasetColumn = ApiSchemas["DatasetExplorationColumnResponse"];
export type SnapshotRowsQuery = ApiSchemas["SnapshotRowsQueryRequest"];
export type SnapshotRows = ApiSchemas["SnapshotRowsResponse"];
export type SnapshotFilter = ApiSchemas["SnapshotFilterPayload"];
export type SnapshotSort = ApiSchemas["SnapshotSortPayload"];
export type ExportFormat = ApiSchemas["ExportFormat"];
export type ExportJob = ApiSchemas["ExportResponse"];
export type ExportCollection = ApiSchemas["ExportCollectionResponse"];

export { DatePipetteApiError };

export async function getCurrentUser(): Promise<CurrentUser> {
  return unwrap(await api.GET("/api/v1/auth/me"));
}

export async function login(email: string, password: string): Promise<CurrentUser> {
  const value = unwrap(
    await api.POST("/api/v1/auth/sessions", {
      body: { email, password },
    }),
  );
  return value.user;
}

export async function logout(): Promise<void> {
  unwrap(
    await api.DELETE("/api/v1/auth/sessions/current", {
      headers: { "X-CSRF-Token": readCookie("datepipette_csrf") },
    }),
  );
}

export async function listDashboards(cursor?: string): Promise<DashboardPage> {
  return unwrap(
    await api.GET("/api/v1/dashboards", {
      params: { query: { limit: 50, cursor } },
    }),
  );
}

export async function getDashboard(dashboardId: string): Promise<Dashboard> {
  return unwrap(
    await api.GET("/api/v1/dashboards/{dashboard_id}", {
      params: { path: { dashboard_id: dashboardId } },
    }),
  );
}

export async function queryDashboardItem(
  dashboardId: string,
  itemId: string,
): Promise<DashboardItemResult> {
  return unwrap(
    await api.POST("/api/v1/dashboards/{dashboard_id}/items/{item_id}/data:query", {
      params: { path: { dashboard_id: dashboardId, item_id: itemId } },
    }),
  );
}

export async function listDatasets(cursor?: string): Promise<DatasetPage> {
  return unwrap(
    await api.GET("/api/v1/datasets", {
      params: { query: { limit: 50, cursor } },
    }),
  );
}

export async function getDatasetExploration(datasetId: string): Promise<DatasetExploration> {
  return unwrap(
    await api.GET("/api/v1/datasets/{dataset_id}/exploration", {
      params: { path: { dataset_id: datasetId } },
    }),
  );
}

export async function queryDatasetRows(
  datasetId: string,
  query: SnapshotRowsQuery,
): Promise<SnapshotRows> {
  return unwrap(
    await api.POST("/api/v1/datasets/{dataset_id}/rows:query", {
      params: { path: { dataset_id: datasetId } },
      body: query,
    }),
  );
}

export async function listDatasetExports(datasetId: string): Promise<ExportCollection> {
  return unwrap(
    await api.GET("/api/v1/datasets/{dataset_id}/exports", {
      params: { path: { dataset_id: datasetId }, query: { limit: 100 } },
    }),
  );
}

export async function createDatasetExport(
  datasetId: string,
  format: ExportFormat,
  query: Pick<SnapshotRowsQuery, "fields" | "filters" | "sorts">,
): Promise<ExportJob> {
  return unwrap(
    await api.POST("/api/v1/datasets/{dataset_id}/exports", {
      params: { path: { dataset_id: datasetId } },
      headers: {
        "X-CSRF-Token": readCookie("datepipette_csrf"),
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: { format, fields: query.fields, filters: query.filters, sorts: query.sorts },
    }),
  );
}

export async function issueExportDownload(
  datasetId: string,
  exportId: string,
): Promise<ApiSchemas["ExportDownloadLinkResponse"]> {
  return unwrap(
    await api.POST("/api/v1/datasets/{dataset_id}/exports/{export_id}/download-link", {
      params: { path: { dataset_id: datasetId, export_id: exportId } },
      headers: { "X-CSRF-Token": readCookie("datepipette_csrf") },
    }),
  );
}

function readCookie(name: string): string {
  const prefix = `${encodeURIComponent(name)}=`;
  const value = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
    ?.slice(prefix.length);
  if (!value) {
    throw new Error("The browser session is missing its CSRF token. Sign in again.");
  }
  return decodeURIComponent(value);
}

export function isUnauthorized(error: unknown): boolean {
  return error instanceof DatePipetteApiError && error.status === 401;
}

export function problemMessage(error: unknown): string {
  if (error instanceof DatePipetteApiError) {
    return error.detail;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "An unexpected error occurred.";
}
