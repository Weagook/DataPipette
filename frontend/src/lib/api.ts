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
