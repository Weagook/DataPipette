import {
  createBrowserClient,
  DatePipetteApiError,
  unwrap,
  type components,
} from "@datepipette/api-client";

const apiBaseUrl =
  import.meta.env.VITE_API_BASE_URL?.trim() || window.location.origin;

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
export type DatasetDefinition =
  | ApiSchemas["VisualDefinitionPayload"]
  | ApiSchemas["SqlDefinitionPayload"];
export type DatasetDraft = ApiSchemas["DatasetDraftResponse"];
export type DatasetValidation = ApiSchemas["DatasetValidationResponse"];
export type DatasetPreview = ApiSchemas["DatasetPreviewResponse"];
export type DatasetImpact = ApiSchemas["DatasetImpactResponse"];
export type Connection = ApiSchemas["ConnectionResponse"];
export type ConnectionPage = ApiSchemas["ConnectionCollectionResponse"];
export type CatalogSchemaPage = ApiSchemas["CatalogSchemaCollectionResponse"];
export type CatalogRelationPage =
  ApiSchemas["CatalogRelationCollectionResponse"];
export type CatalogRelation = ApiSchemas["CatalogRelationDetailResponse"];
export type CatalogJoinSuggestion = ApiSchemas["CatalogJoinSuggestionResponse"];
export type RefreshRun = ApiSchemas["RefreshRunResponse"];
export type RefreshSchedule = ApiSchemas["RefreshScheduleResponse"];
export type RefreshScheduleInput = ApiSchemas["ReplaceRefreshScheduleRequest"];
export type DatasetExploration = ApiSchemas["DatasetExplorationResponse"];
export type DatasetColumn = ApiSchemas["DatasetExplorationColumnResponse"];
export type SnapshotRowsQuery = ApiSchemas["SnapshotRowsQueryRequest"];
export type SnapshotRows = ApiSchemas["SnapshotRowsResponse"];
export type SnapshotFilter = ApiSchemas["SnapshotFilterPayload"];
export type SnapshotSort = ApiSchemas["SnapshotSortPayload"];
export type ExportFormat = ApiSchemas["ExportFormat"];
export type ExportJob = ApiSchemas["ExportResponse"];
export type ExportCollection = ApiSchemas["ExportCollectionResponse"];
export type Chart = ApiSchemas["ChartResponse"];
export type ChartPage = ApiSchemas["ChartCollectionResponse"];
export type ChartDefinition = ApiSchemas["ChartDefinitionPayload"];
export type ChartQueryResult = ApiSchemas["ChartQueryResponse"];

export { DatePipetteApiError };

export async function getCurrentUser(): Promise<CurrentUser> {
  return unwrap(await api.GET("/api/v1/auth/me"));
}

export async function login(
  email: string,
  password: string,
): Promise<CurrentUser> {
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
    await api.POST(
      "/api/v1/dashboards/{dashboard_id}/items/{item_id}/data:query",
      {
        params: { path: { dashboard_id: dashboardId, item_id: itemId } },
      },
    ),
  );
}

export async function listDatasets(cursor?: string): Promise<DatasetPage> {
  return unwrap(
    await api.GET("/api/v1/datasets", {
      params: { query: { limit: 50, cursor } },
    }),
  );
}

export async function listCharts(cursor?: string): Promise<ChartPage> {
  return unwrap(
    await api.GET("/api/v1/charts", {
      params: { query: { limit: 50, cursor } },
    }),
  );
}

export async function getChart(
  chartId: string,
): Promise<{ chart: Chart; etag: string }> {
  const result = await api.GET("/api/v1/charts/{chart_id}", {
    params: { path: { chart_id: chartId } },
  });
  const chart = unwrap(result);
  return {
    chart,
    etag: result.response.headers.get("etag") ?? `"chart-${chart.revision}"`,
  };
}

export async function createChart(
  name: string,
  datasetId: string,
  definition: ChartDefinition,
): Promise<{ chart: Chart; etag: string }> {
  const result = await api.POST("/api/v1/charts", {
    headers: { "X-CSRF-Token": readCookie("datepipette_csrf") },
    body: { name, dataset_id: datasetId, definition },
  });
  const chart = unwrap(result);
  return {
    chart,
    etag: result.response.headers.get("etag") ?? `"chart-${chart.revision}"`,
  };
}

export async function replaceChart(
  chartId: string,
  name: string,
  definition: ChartDefinition,
  etag: string,
): Promise<{ chart: Chart; etag: string }> {
  const result = await api.PUT("/api/v1/charts/{chart_id}", {
    params: { path: { chart_id: chartId } },
    headers: {
      "X-CSRF-Token": readCookie("datepipette_csrf"),
      "If-Match": etag,
    },
    body: { name, definition },
  });
  const chart = unwrap(result);
  return {
    chart,
    etag: result.response.headers.get("etag") ?? `"chart-${chart.revision}"`,
  };
}

export async function queryChart(chartId: string): Promise<ChartQueryResult> {
  return unwrap(
    await api.POST("/api/v1/charts/{chart_id}/data:query", {
      params: { path: { chart_id: chartId } },
    }),
  );
}

export async function getDataset(datasetId: string): Promise<Dataset> {
  return unwrap(
    await api.GET("/api/v1/datasets/{dataset_id}", {
      params: { path: { dataset_id: datasetId } },
    }),
  );
}

export async function listConnections(
  cursor?: string,
): Promise<ConnectionPage> {
  return unwrap(
    await api.GET("/api/v1/connections", {
      params: { query: { limit: 100, cursor } },
    }),
  );
}

export async function listCatalogSchemas(
  connectionId: string,
): Promise<CatalogSchemaPage> {
  return unwrap(
    await api.GET("/api/v1/connections/{connection_id}/catalog/schemas", {
      params: { path: { connection_id: connectionId }, query: { limit: 100 } },
    }),
  );
}

export async function listCatalogRelations(
  connectionId: string,
  schema: string,
): Promise<CatalogRelationPage> {
  return unwrap(
    await api.GET(
      "/api/v1/connections/{connection_id}/catalog/schemas/{schema_name}/relations",
      {
        params: {
          path: { connection_id: connectionId, schema_name: schema },
          query: { limit: 100 },
        },
      },
    ),
  );
}

export async function getCatalogRelation(
  connectionId: string,
  schema: string,
  relation: string,
): Promise<CatalogRelation> {
  return unwrap(
    await api.GET(
      "/api/v1/connections/{connection_id}/catalog/schemas/{schema_name}/relations/{relation_name}",
      {
        params: {
          path: {
            connection_id: connectionId,
            schema_name: schema,
            relation_name: relation,
          },
        },
      },
    ),
  );
}

export async function suggestCatalogJoins(
  connectionId: string,
  relations: { id: string; schema: string; name: string }[],
): Promise<CatalogJoinSuggestion[]> {
  const value = unwrap(
    await api.POST(
      "/api/v1/connections/{connection_id}/catalog/join-suggestions",
      {
        params: { path: { connection_id: connectionId } },
        body: { relations },
      },
    ),
  );
  return value.data;
}

export async function createDataset(
  name: string,
  connectionId: string,
  definition: DatasetDefinition,
): Promise<Dataset> {
  return unwrap(
    await api.POST("/api/v1/datasets", {
      headers: { "X-CSRF-Token": readCookie("datepipette_csrf") },
      body: { name, connection_id: connectionId, definition },
    }),
  );
}

export async function renameDataset(
  datasetId: string,
  name: string,
): Promise<Dataset> {
  return unwrap(
    await api.PATCH("/api/v1/datasets/{dataset_id}", {
      params: { path: { dataset_id: datasetId } },
      headers: { "X-CSRF-Token": readCookie("datepipette_csrf") },
      body: { name },
    }),
  );
}

export async function getDatasetDraft(
  datasetId: string,
): Promise<{ draft: DatasetDraft; etag: string }> {
  const result = await api.GET("/api/v1/datasets/{dataset_id}/draft", {
    params: { path: { dataset_id: datasetId } },
  });
  const draft = unwrap(result);
  return {
    draft,
    etag: result.response.headers.get("etag") ?? `"draft-${draft.revision}"`,
  };
}

export async function replaceDatasetDraft(
  datasetId: string,
  definition: DatasetDefinition,
  etag: string,
): Promise<{ draft: DatasetDraft; etag: string }> {
  const result = await api.PUT("/api/v1/datasets/{dataset_id}/draft", {
    params: { path: { dataset_id: datasetId } },
    headers: {
      "X-CSRF-Token": readCookie("datepipette_csrf"),
      "If-Match": etag,
    },
    body: { definition },
  });
  const draft = unwrap(result);
  return {
    draft,
    etag: result.response.headers.get("etag") ?? `"draft-${draft.revision}"`,
  };
}

export async function validateDatasetDraft(
  datasetId: string,
  etag: string,
  overrideReason?: string,
): Promise<DatasetValidation> {
  return unwrap(
    await api.POST("/api/v1/datasets/{dataset_id}/draft/validations", {
      params: { path: { dataset_id: datasetId } },
      headers: {
        "X-CSRF-Token": readCookie("datepipette_csrf"),
        "If-Match": etag,
      },
      ...(overrideReason ? { body: { override_reason: overrideReason } } : {}),
    }),
  );
}

export async function previewDatasetDraft(
  datasetId: string,
  etag: string,
  limit: number,
): Promise<DatasetPreview> {
  return unwrap(
    await api.POST("/api/v1/datasets/{dataset_id}/draft/previews", {
      params: { path: { dataset_id: datasetId } },
      headers: {
        "X-CSRF-Token": readCookie("datepipette_csrf"),
        "If-Match": etag,
      },
      body: { limit },
    }),
  );
}

export async function analyzeDatasetImpact(
  datasetId: string,
  etag: string,
): Promise<DatasetImpact> {
  return unwrap(
    await api.POST("/api/v1/datasets/{dataset_id}/draft/impact-analyses", {
      params: { path: { dataset_id: datasetId } },
      headers: {
        "X-CSRF-Token": readCookie("datepipette_csrf"),
        "If-Match": etag,
      },
    }),
  );
}

export async function requestDatasetRefresh(
  datasetId: string,
  etag: string,
): Promise<RefreshRun> {
  return unwrap(
    await api.POST("/api/v1/datasets/{dataset_id}/refresh-runs", {
      params: { path: { dataset_id: datasetId } },
      headers: {
        "X-CSRF-Token": readCookie("datepipette_csrf"),
        "If-Match": etag,
        "Idempotency-Key": crypto.randomUUID(),
      },
    }),
  );
}

export async function getRefreshRun(
  datasetId: string,
  runId: string,
): Promise<RefreshRun> {
  return unwrap(
    await api.GET("/api/v1/datasets/{dataset_id}/refresh-runs/{run_id}", {
      params: { path: { dataset_id: datasetId, run_id: runId } },
    }),
  );
}

export async function getRefreshSchedule(
  datasetId: string,
): Promise<RefreshSchedule> {
  return unwrap(
    await api.GET("/api/v1/datasets/{dataset_id}/refresh-schedule", {
      params: { path: { dataset_id: datasetId } },
    }),
  );
}

export async function replaceRefreshSchedule(
  datasetId: string,
  value: RefreshScheduleInput,
  revision?: number,
): Promise<RefreshSchedule> {
  return unwrap(
    await api.PUT("/api/v1/datasets/{dataset_id}/refresh-schedule", {
      params: { path: { dataset_id: datasetId } },
      headers: {
        "X-CSRF-Token": readCookie("datepipette_csrf"),
        ...(revision ? { "If-Match": `"schedule-${revision}"` } : {}),
      },
      body: value,
    }),
  );
}

export async function getDatasetExploration(
  datasetId: string,
): Promise<DatasetExploration> {
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

export async function listDatasetExports(
  datasetId: string,
): Promise<ExportCollection> {
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
      body: {
        format,
        fields: query.fields,
        filters: query.filters,
        sorts: query.sorts,
      },
    }),
  );
}

export async function issueExportDownload(
  datasetId: string,
  exportId: string,
): Promise<ApiSchemas["ExportDownloadLinkResponse"]> {
  return unwrap(
    await api.POST(
      "/api/v1/datasets/{dataset_id}/exports/{export_id}/download-link",
      {
        params: { path: { dataset_id: datasetId, export_id: exportId } },
        headers: { "X-CSRF-Token": readCookie("datepipette_csrf") },
      },
    ),
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
    throw new Error(
      "The browser session is missing its CSRF token. Sign in again.",
    );
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
