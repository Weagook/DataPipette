import {
  createServiceTokenClient,
  DataPipetteApiError,
  unwrap,
} from "@datapipette/api-client";

const baseUrl = process.env.DATAPIPETTE_BASE_URL ?? "http://127.0.0.1:8000";
const token = process.env.DATAPIPETTE_API_TOKEN;

if (!token) {
  throw new Error("Set DATAPIPETTE_API_TOKEN to a dashboards:read service token.");
}

const client = createServiceTokenClient({ baseUrl, token });

try {
  const dashboardPage = unwrap(
    await client.GET("/api/v1/dashboards", {
      params: { query: { limit: 20 } },
    }),
  );
  const dashboard = dashboardPage.data[0];
  if (!dashboard) {
    console.log("No dashboard is granted to this service account.");
    process.exit(0);
  }

  const fullDashboard = unwrap(
    await client.GET("/api/v1/dashboards/{dashboard_id}", {
      params: { path: { dashboard_id: dashboard.id } },
    }),
  );
  console.log(`Dashboard: ${fullDashboard.name}`);

  for (const item of fullDashboard.definition.items ?? []) {
    const result = unwrap(
      await client.POST("/api/v1/dashboards/{dashboard_id}/items/{item_id}/data:query", {
        params: {
          path: { dashboard_id: fullDashboard.id, item_id: item.id },
        },
      }),
    );
    console.log(item.id, result.data.rows);
  }
} catch (error) {
  if (error instanceof DataPipetteApiError) {
    console.error(error.code, error.detail, { requestId: error.requestId });
    process.exit(1);
  }
  throw error;
}
