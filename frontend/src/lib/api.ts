import {
  createBrowserClient,
  DataPipetteApiError,
  unwrap,
  type components,
} from "@datapipette/api-client";

import { randomUuid } from "./id";

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
export type DashboardDefinition = ApiSchemas["DashboardDefinitionPayload"];
export type DashboardItem = ApiSchemas["DashboardItemPayload"];
export type DashboardGrant = ApiSchemas["DashboardGrantResponse"];
export type GrantPrincipalType = ApiSchemas["GrantPrincipalType"];
export type UserAdmin = ApiSchemas["UserAdminResponse"];
export type Group = ApiSchemas["GroupResponse"];
export type SystemRole = ApiSchemas["SystemRole"];
export type AccountStatus = ApiSchemas["AccountStatus"];
export type Membership = ApiSchemas["MembershipResponse"];
export type ServiceAccount = ApiSchemas["ServiceAccountResponse"];
export type ApiToken = ApiSchemas["ApiTokenResponse"];
export type IssuedApiToken = ApiSchemas["IssuedApiTokenResponse"];
export type ApiTokenScope = ApiSchemas["ApiTokenScope"];
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
export type ConnectionCreate = ApiSchemas["CreateConnectionRequest"];
export type ConnectionUpdate = ApiSchemas["UpdateConnectionRequest"];
export type ConnectionTest = ApiSchemas["ConnectionTestResponse"];
export type ConnectionGrant = ApiSchemas["ConnectionGrantResponse"];
export type ConnectionGrantCreate = ApiSchemas["ConnectionGrantCreateRequest"];
export type ConnectionScopeType = ApiSchemas["ConnectionScopeType"];
export type PostgresSslMode = ApiSchemas["PostgresSslMode"];
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
export type QuerySafetyPolicy = ApiSchemas["QuerySafetyPolicyResponse"];
export type QuerySafetyPolicyInput =
  ApiSchemas["ReplaceQuerySafetyPolicyRequest"];
export type AuditEvent = ApiSchemas["AuditEventResponse"];
export type AuditEventPage = ApiSchemas["AuditEventCollectionResponse"];
export type AuditActorType = ApiSchemas["AuditActorType"];
export type AuditOutcome = ApiSchemas["AuditOutcome"];

export interface AuditFilters {
  action?: string;
  actorId?: string;
  actorType?: AuditActorType;
  occurredFrom?: string;
  occurredTo?: string;
  outcome?: AuditOutcome;
  requestId?: string;
  resourceId?: string;
  resourceType?: string;
}

export { DataPipetteApiError };

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
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
    }),
  );
}

export async function updateCurrentUserLocale(
  locale: "en" | "ru",
): Promise<CurrentUser> {
  return unwrap(
    await api.PATCH("/api/v1/auth/me/preferences", {
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
      body: { locale },
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

export async function getDashboardForEdit(
  dashboardId: string,
): Promise<{ dashboard: Dashboard; etag: string }> {
  const result = await api.GET("/api/v1/dashboards/{dashboard_id}", {
    params: { path: { dashboard_id: dashboardId } },
  });
  const dashboard = unwrap(result);
  return {
    dashboard,
    etag:
      result.response.headers.get("etag") ?? `"dashboard-${dashboard.revision}"`,
  };
}

export async function createDashboard(
  name: string,
  definition: DashboardDefinition,
): Promise<{ dashboard: Dashboard; etag: string }> {
  const result = await api.POST("/api/v1/dashboards", {
    headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
    body: { name, definition },
  });
  const dashboard = unwrap(result);
  return {
    dashboard,
    etag:
      result.response.headers.get("etag") ?? `"dashboard-${dashboard.revision}"`,
  };
}

export async function replaceDashboard(
  dashboardId: string,
  name: string,
  definition: DashboardDefinition,
  etag: string,
): Promise<{ dashboard: Dashboard; etag: string }> {
  const result = await api.PUT("/api/v1/dashboards/{dashboard_id}", {
    params: { path: { dashboard_id: dashboardId } },
    headers: {
      "X-CSRF-Token": readCookie("datapipette_csrf"),
      "If-Match": etag,
    },
    body: { name, definition },
  });
  const dashboard = unwrap(result);
  return {
    dashboard,
    etag:
      result.response.headers.get("etag") ?? `"dashboard-${dashboard.revision}"`,
  };
}

export async function listDashboardGrants(
  dashboardId: string,
): Promise<DashboardGrant[]> {
  const value = unwrap(
    await api.GET("/api/v1/dashboards/{dashboard_id}/grants", {
      params: { path: { dashboard_id: dashboardId } },
    }),
  );
  return value.data;
}

export async function createDashboardGrant(
  dashboardId: string,
  principalType: GrantPrincipalType,
  principalId: string,
): Promise<DashboardGrant> {
  return unwrap(
    await api.POST("/api/v1/dashboards/{dashboard_id}/grants", {
      params: { path: { dashboard_id: dashboardId } },
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
      body: { principal_type: principalType, principal_id: principalId },
    }),
  );
}

export async function revokeDashboardGrant(
  dashboardId: string,
  grantId: string,
): Promise<void> {
  unwrap(
    await api.DELETE("/api/v1/dashboards/{dashboard_id}/grants/{grant_id}", {
      params: { path: { dashboard_id: dashboardId, grant_id: grantId } },
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
    }),
  );
}

export async function listUsers(): Promise<UserAdmin[]> {
  const value = unwrap(
    await api.GET("/api/v1/users", {
      params: { query: { limit: 100 } },
    }),
  );
  return value.data;
}

export async function listGroups(): Promise<Group[]> {
  const value = unwrap(
    await api.GET("/api/v1/groups", {
      params: { query: { limit: 100 } },
    }),
  );
  return value.data;
}

export async function createUser(input: ApiSchemas["CreateUserRequest"]): Promise<UserAdmin> {
  return unwrap(
    await api.POST("/api/v1/users", {
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
      body: input,
    }),
  );
}

export async function updateUser(
  userId: string,
  input: ApiSchemas["UpdateUserRequest"],
): Promise<UserAdmin> {
  return unwrap(
    await api.PATCH("/api/v1/users/{user_id}", {
      params: { path: { user_id: userId } },
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
      body: input,
    }),
  );
}

export async function revokeUserSessions(userId: string): Promise<number> {
  const value = unwrap(
    await api.DELETE("/api/v1/users/{user_id}/sessions", {
      params: { path: { user_id: userId } },
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
    }),
  );
  return value.revoked_count;
}

export async function createGroup(name: string): Promise<Group> {
  return unwrap(
    await api.POST("/api/v1/groups", {
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
      body: { name },
    }),
  );
}

export async function renameGroup(groupId: string, name: string): Promise<Group> {
  return unwrap(
    await api.PATCH("/api/v1/groups/{group_id}", {
      params: { path: { group_id: groupId } },
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
      body: { name },
    }),
  );
}

export async function listGroupMembers(groupId: string): Promise<Membership[]> {
  const value = unwrap(
    await api.GET("/api/v1/groups/{group_id}/members", {
      params: { path: { group_id: groupId } },
    }),
  );
  return value.data;
}

export async function addGroupMember(groupId: string, userId: string): Promise<Membership> {
  return unwrap(
    await api.PUT("/api/v1/groups/{group_id}/members/{user_id}", {
      params: { path: { group_id: groupId, user_id: userId } },
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
    }),
  );
}

export async function removeGroupMember(groupId: string, userId: string): Promise<void> {
  unwrap(
    await api.DELETE("/api/v1/groups/{group_id}/members/{user_id}", {
      params: { path: { group_id: groupId, user_id: userId } },
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
    }),
  );
}

export async function listServiceAccounts(): Promise<ServiceAccount[]> {
  const value = unwrap(
    await api.GET("/api/v1/service-accounts", {
      params: { query: { limit: 100 } },
    }),
  );
  return value.data;
}

export async function createServiceAccount(
  name: string,
  role: Exclude<SystemRole, "admin">,
): Promise<ServiceAccount> {
  return unwrap(
    await api.POST("/api/v1/service-accounts", {
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
      body: { name, role },
    }),
  );
}

export async function updateServiceAccount(
  accountId: string,
  input: ApiSchemas["UpdateServiceAccountRequest"],
): Promise<ServiceAccount> {
  return unwrap(
    await api.PATCH("/api/v1/service-accounts/{account_id}", {
      params: { path: { account_id: accountId } },
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
      body: input,
    }),
  );
}

export async function listApiTokens(accountId: string): Promise<ApiToken[]> {
  const value = unwrap(
    await api.GET("/api/v1/service-accounts/{account_id}/tokens", {
      params: { path: { account_id: accountId } },
    }),
  );
  return value.data;
}

export async function createApiToken(
  accountId: string,
  input: ApiSchemas["CreateApiTokenRequest"],
): Promise<IssuedApiToken> {
  return unwrap(
    await api.POST("/api/v1/service-accounts/{account_id}/tokens", {
      params: { path: { account_id: accountId } },
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
      body: input,
    }),
  );
}

export async function revokeApiToken(accountId: string, tokenId: string): Promise<void> {
  unwrap(
    await api.DELETE("/api/v1/service-accounts/{account_id}/tokens/{token_id}", {
      params: { path: { account_id: accountId, token_id: tokenId } },
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
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
    headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
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
      "X-CSRF-Token": readCookie("datapipette_csrf"),
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

export async function getConnection(connectionId: string): Promise<Connection> {
  return unwrap(
    await api.GET("/api/v1/connections/{connection_id}", {
      params: { path: { connection_id: connectionId } },
    }),
  );
}

export async function createConnection(
  input: ConnectionCreate,
): Promise<Connection> {
  return unwrap(
    await api.POST("/api/v1/connections", {
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
      body: input,
    }),
  );
}

export async function updateConnection(
  connectionId: string,
  input: ConnectionUpdate,
): Promise<Connection> {
  return unwrap(
    await api.PATCH("/api/v1/connections/{connection_id}", {
      params: { path: { connection_id: connectionId } },
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
      body: input,
    }),
  );
}

export async function testConnection(
  connectionId: string,
): Promise<ConnectionTest> {
  return unwrap(
    await api.POST("/api/v1/connections/{connection_id}/tests", {
      params: { path: { connection_id: connectionId } },
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
    }),
  );
}

export async function listConnectionGrants(
  connectionId: string,
): Promise<ConnectionGrant[]> {
  const value = unwrap(
    await api.GET("/api/v1/connections/{connection_id}/grants", {
      params: { path: { connection_id: connectionId } },
    }),
  );
  return value.data;
}

export async function createConnectionGrant(
  connectionId: string,
  input: ConnectionGrantCreate,
): Promise<ConnectionGrant> {
  return unwrap(
    await api.POST("/api/v1/connections/{connection_id}/grants", {
      params: { path: { connection_id: connectionId } },
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
      body: input,
    }),
  );
}

export async function revokeConnectionGrant(
  connectionId: string,
  grantId: string,
): Promise<void> {
  unwrap(
    await api.DELETE(
      "/api/v1/connections/{connection_id}/grants/{grant_id}",
      {
        params: {
          path: { connection_id: connectionId, grant_id: grantId },
        },
        headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
      },
    ),
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
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
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
      headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
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
      "X-CSRF-Token": readCookie("datapipette_csrf"),
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
        "X-CSRF-Token": readCookie("datapipette_csrf"),
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
        "X-CSRF-Token": readCookie("datapipette_csrf"),
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
        "X-CSRF-Token": readCookie("datapipette_csrf"),
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
        "X-CSRF-Token": readCookie("datapipette_csrf"),
        "If-Match": etag,
        "Idempotency-Key": randomUuid(),
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
        "X-CSRF-Token": readCookie("datapipette_csrf"),
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
        "X-CSRF-Token": readCookie("datapipette_csrf"),
        "Idempotency-Key": randomUuid(),
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
        headers: { "X-CSRF-Token": readCookie("datapipette_csrf") },
      },
    ),
  );
}

export async function getQuerySafetyPolicy(): Promise<{
  policy: QuerySafetyPolicy;
  etag: string;
}> {
  const result = await api.GET("/api/v1/query-safety-policy");
  const policy = unwrap(result);
  return {
    policy,
    etag:
      result.response.headers.get("etag") ??
      `"query-safety-policy-${policy.revision}"`,
  };
}

export async function replaceQuerySafetyPolicy(
  input: QuerySafetyPolicyInput,
  etag: string,
): Promise<{ policy: QuerySafetyPolicy; etag: string }> {
  const result = await api.PUT("/api/v1/query-safety-policy", {
    headers: {
      "If-Match": etag,
      "X-CSRF-Token": readCookie("datapipette_csrf"),
    },
    body: input,
  });
  const policy = unwrap(result);
  return {
    policy,
    etag:
      result.response.headers.get("etag") ??
      `"query-safety-policy-${policy.revision}"`,
  };
}

export async function listAuditEvents(
  filters: AuditFilters,
  cursor?: string,
): Promise<AuditEventPage> {
  return unwrap(
    await api.GET("/api/v1/audit-events", {
      params: {
        query: {
          action: filters.action || undefined,
          actor_id: filters.actorId || undefined,
          actor_type: filters.actorType,
          cursor,
          limit: 30,
          occurred_from: filters.occurredFrom,
          occurred_to: filters.occurredTo,
          outcome: filters.outcome,
          request_id: filters.requestId || undefined,
          resource_id: filters.resourceId || undefined,
          resource_type: filters.resourceType || undefined,
        },
      },
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
    throw new Error(
      "The browser session is missing its CSRF token. Sign in again.",
    );
  }
  return decodeURIComponent(value);
}

export function isUnauthorized(error: unknown): boolean {
  return error instanceof DataPipetteApiError && error.status === 401;
}

export function problemMessage(error: unknown): string {
  if (error instanceof DataPipetteApiError) {
    return error.detail;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "An unexpected error occurred.";
}
