import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import { Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { FullPageStatus } from "./components/Status";
import { getCurrentUser, isUnauthorized } from "./lib/api";
import { queryKeys } from "./lib/query";
const DashboardDetailPage = lazy(() =>
  import("./pages/DashboardDetailPage").then((module) => ({ default: module.DashboardDetailPage })),
);
const DashboardsPage = lazy(() =>
  import("./pages/DashboardsPage").then((module) => ({ default: module.DashboardsPage })),
);
const DatasetsPage = lazy(() =>
  import("./pages/DatasetsPage").then((module) => ({ default: module.DatasetsPage })),
);
const DatasetTablePage = lazy(() =>
  import("./pages/DatasetTablePage").then((module) => ({ default: module.DatasetTablePage })),
);
const LoginPage = lazy(() =>
  import("./pages/LoginPage").then((module) => ({ default: module.LoginPage })),
);
const OverviewPage = lazy(() =>
  import("./pages/OverviewPage").then((module) => ({ default: module.OverviewPage })),
);

export function App() {
  return (
    <Suspense fallback={<FullPageStatus label="Opening DatePipette" />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<ProtectedLayout />}>
          <Route index element={<Navigate to="/overview" replace />} />
          <Route path="/overview" element={<OverviewPage />} />
          <Route path="/dashboards" element={<DashboardsPage />} />
          <Route path="/dashboards/:dashboardId" element={<DashboardDetailPage />} />
          <Route path="/datasets" element={<DatasetsPage />} />
          <Route path="/datasets/:datasetId" element={<DatasetTablePage />} />
          <Route path="/datasets/:datasetId/table" element={<DatasetTablePage />} />
        </Route>
        <Route path="*" element={<Navigate to="/overview" replace />} />
      </Routes>
    </Suspense>
  );
}

function ProtectedLayout() {
  const location = useLocation();
  const session = useQuery({
    queryKey: queryKeys.session,
    queryFn: getCurrentUser,
    retry: false,
    staleTime: 60_000,
  });

  if (session.isPending) {
    return <FullPageStatus label="Opening your workspace" />;
  }
  if (session.isError) {
    if (isUnauthorized(session.error)) {
      return <Navigate to="/login" state={{ from: location.pathname }} replace />;
    }
    return (
      <FullPageStatus
        label="The workspace is temporarily unavailable"
        detail="Check the API connection and try again."
        actionLabel="Try again"
        onAction={() => void session.refetch()}
      />
    );
  }
  return (
    <AppShell user={session.data}>
      <Outlet context={session.data} />
    </AppShell>
  );
}
