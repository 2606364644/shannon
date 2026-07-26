import { createBrowserRouter, useNavigate, useParams } from "react-router-dom";
import { useEffect } from "react";
import { WorkspaceListPage } from "./pages/WorkspaceListPage";
import { ReposPage } from "./pages/ReposPage";
import { RepoDetailPage } from "./pages/RepoDetailPage";
import { ScanNewPage } from "./pages/ScanNewPage";
import { DashboardPage } from "./pages/DashboardPage";
import { SettingsPage } from "./pages/SettingsPage";
import WorkspaceDetail from "./routes/WorkspaceDetail";
import { OverviewTab } from "./routes/WorkspaceDetail/OverviewTab";
import { ReportTab } from "./routes/WorkspaceDetail/ReportTab";
import { DeliverablesTab } from "./routes/WorkspaceDetail/DeliverablesTab";
import { LogsTab } from "./routes/WorkspaceDetail/LogsTab";
import LiveTab from "./routes/WorkspaceDetail/LiveTab";
import { apiGet } from "./api/client";
import type { SessionData } from "./api/types";
import { AppShell } from "./components/layout/AppShell";
import { DevComponentsPage } from "./pages/DevComponentsPage";
import LoginPage from "./pages/LoginPage";

// 默认 tab：进行中 → live，完成 → report。fetch status 后 navigate（replace 避免占历史栈）。
function DefaultTab() {
  const { workspace } = useParams<{ workspace: string }>();
  const nav = useNavigate();
  useEffect(() => {
    apiGet<SessionData>(`/workspaces/${workspace}`).then((s) => {
      const st = s.status ?? s.session?.status ?? "running";
      nav(st === "completed" || st === "done" ? "report" : "live", { replace: true });
    }).catch(() => nav("live", { replace: true }));
  }, [workspace, nav]);
  return null;
}

const devRoutes = import.meta.env.DEV
  ? [{ path: "/dev/components", element: <DevComponentsPage /> }]
  : [];

export const router = createBrowserRouter([
  // /login 公开路由（不进 AppShell；业务路由的 RequireAuth 包裹在 Task 16 加）
  { path: "/login", element: <LoginPage /> },
  {
    element: <AppShell />,
    children: [
      { path: "/", element: <DashboardPage /> },
      { path: "/workspaces", element: <WorkspaceListPage /> },
      { path: "/repos", element: <ReposPage /> },
      { path: "/repos/*", element: <RepoDetailPage /> },
      { path: "/scan/new", element: <ScanNewPage /> },
      {
        path: "/p/:workspace",
        element: <WorkspaceDetail />,
        children: [
          { index: true, element: <DefaultTab /> },
          { path: "overview", element: <OverviewTab /> },
          { path: "report", element: <ReportTab /> },
          { path: "deliverables", element: <DeliverablesTab /> },
          { path: "logs", element: <LogsTab /> },
          { path: "live", element: <LiveTab /> },
        ],
      },
      { path: "/settings", element: <SettingsPage /> },
      ...devRoutes,
    ],
  },
]);
