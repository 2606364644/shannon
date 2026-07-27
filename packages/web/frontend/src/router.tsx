import { createBrowserRouter, useNavigate, useParams } from "react-router-dom";
import { useEffect } from "react";
import { WorkspaceListPage } from "./pages/WorkspaceListPage";
import { ScanNewPage } from "./pages/ScanNewPage";
import { DashboardPage } from "./pages/DashboardPage";
import { SettingsPage } from "./pages/SettingsPage";
import WorkspaceDetail from "./routes/WorkspaceDetail";
import { ScanList } from "./routes/WorkspaceDetail/ScanList";
import ScanDetail from "./routes/WorkspaceDetail/ScanDetail";
import { OverviewTab } from "./routes/WorkspaceDetail/OverviewTab";
import { ReportTab } from "./routes/WorkspaceDetail/ReportTab";
import { DeliverablesTab } from "./routes/WorkspaceDetail/DeliverablesTab";
import { LogsTab } from "./routes/WorkspaceDetail/LogsTab";
import LiveTab from "./routes/WorkspaceDetail/LiveTab";
import { ReposTab } from "./routes/WorkspaceDetail/ReposTab";
import WsSettingsTab from "./routes/WorkspaceDetail/WsSettingsTab";
import { getScan, listScans } from "./api/client";
import { AppShell } from "./components/layout/AppShell";
import { DevComponentsPage } from "./pages/DevComponentsPage";
import LoginPage from "./pages/LoginPage";
import { RequireAuth } from "./auth/RequireAuth";
import { UsersPage } from "./pages/UsersPage";
import { RequireAdmin } from "./auth/RequireAdmin";
import { WorkspacesEntry } from "./components/WorkspacesEntry";

// per-scan 默认 tab：进行中 -> live，完成 -> report。fetch scan status 后 navigate（replace 避免占历史栈）。
function DefaultScanTab() {
  const { workspace, scanId } = useParams<{ workspace: string; scanId: string }>();
  const nav = useNavigate();
  useEffect(() => {
    if (!workspace || !scanId) return;
    getScan(workspace, scanId)
      .then((s) => {
        const st = s.status ?? s.session?.status ?? "running";
        nav(st === "completed" || st === "done" ? "report" : "live", { replace: true });
      })
      .catch(() => nav("live", { replace: true }));
  }, [workspace, scanId, nav]);
  return null;
}

// 旧 ws-scoped tab 路由（/p/:ws/overview 等）过渡期 shim：redirect 到 latest scan 的对应 tab。
// spec §5.2 shim：旧端点操作 ws 内 latest scan。Phase 2 切完后此 redirect 移除（F6）。
function LegacyWsTabRedirect({ tab }: { tab: string }) {
  const { workspace } = useParams<{ workspace: string }>();
  const nav = useNavigate();
  useEffect(() => {
    if (!workspace) return;
    listScans(workspace)
      .then((scans) => {
        if (scans.length === 0) { nav(`/p/${workspace}`, { replace: true }); return; }
        // listScans 按 created_at 倒序，首项 = latest scan
        nav(`/p/${workspace}/scans/${scans[0].scan_id}/${tab}`, { replace: true });
      })
      .catch(() => nav(`/p/${workspace}`, { replace: true }));
  }, [workspace, tab, nav]);
  return null;
}

const devRoutes = import.meta.env.DEV
  ? [{ path: "/dev/components", element: <DevComponentsPage /> }]
  : [];

export const router = createBrowserRouter([
  // /login 公开路由（不进 AppShell；业务路由的 RequireAuth 包裹在 Task 16 加）
  { path: "/login", element: <LoginPage /> },
  {
    element: <RequireAuth><AppShell /></RequireAuth>,
    children: [
      { path: "/", element: <DashboardPage /> },
      // 顶栏「工作区」入口：三段跳转（pinned->最近->空态）。IA 重设计 §2.3
      { path: "/workspaces-entry", element: <WorkspacesEntry /> },
      // 工作区管理页（admin 专属）：列表 + CRUD。普通用户走 /workspaces-entry 跳转。
      { path: "/workspaces", element: <RequireAdmin><WorkspaceListPage /></RequireAdmin> },
      { path: "/scan/new", element: <ScanNewPage /> },
      {
        // ws 概览（容器）：ws header + 扫描列表 + 仓库/settings 入口
        path: "/p/:workspace",
        element: <WorkspaceDetail />,
        children: [
          { index: true, element: <ScanList /> },
          // ws 级 tab（仓库/配置）保留在 ws 概览下
          { path: "repos", element: <ReposTab /> },
          { path: "settings", element: <WsSettingsTab /> },
          // 旧 ws-scoped scan tab 路由 -> redirect 到 latest scan 对应 tab（过渡期 shim）
          { path: "overview", element: <LegacyWsTabRedirect tab="overview" /> },
          { path: "report", element: <LegacyWsTabRedirect tab="report" /> },
          { path: "deliverables", element: <LegacyWsTabRedirect tab="deliverables" /> },
          { path: "logs", element: <LegacyWsTabRedirect tab="logs" /> },
          { path: "live", element: <LegacyWsTabRedirect tab="live" /> },
        ],
      },
      {
        // per-scan 视图：scan header + scan tabs（overview/report/deliverables/logs/live）
        path: "/p/:workspace/scans/:scanId",
        element: <ScanDetail />,
        children: [
          { index: true, element: <DefaultScanTab /> },
          { path: "overview", element: <OverviewTab /> },
          { path: "report", element: <ReportTab /> },
          { path: "deliverables", element: <DeliverablesTab /> },
          { path: "logs", element: <LogsTab /> },
          { path: "live", element: <LiveTab /> },
        ],
      },
      { path: "/settings", element: <SettingsPage /> },
      { path: "/users", element: <RequireAdmin><UsersPage /></RequireAdmin> },
      ...devRoutes,
    ],
  },
]);
