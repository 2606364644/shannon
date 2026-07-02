import { createBrowserRouter, useNavigate, useParams } from "react-router-dom";
import { useEffect } from "react";
import { WorkspaceListPage } from "./pages/WorkspaceListPage";
import { ScanNewPage } from "./pages/ScanNewPage";
import WorkspaceDetail from "./routes/WorkspaceDetail";
import { OverviewTab } from "./routes/WorkspaceDetail/OverviewTab";
import { ReportTab } from "./routes/WorkspaceDetail/ReportTab";
import { DeliverablesTab } from "./routes/WorkspaceDetail/DeliverablesTab";
import { LogsTab } from "./routes/WorkspaceDetail/LogsTab";
import { LiveTab } from "./routes/WorkspaceDetail/LiveTab";
import { apiGet } from "./api/client";
import type { SessionData } from "./api/types";

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

export const router = createBrowserRouter([
  { path: "/", element: <WorkspaceListPage /> },
  { path: "/scan/new", element: <ScanNewPage /> },
  { path: "/p/:workspace", element: <WorkspaceDetail />, children: [
    { index: true, element: <DefaultTab /> },
    { path: "overview", element: <OverviewTab /> },
    { path: "report", element: <ReportTab /> },
    { path: "deliverables", element: <DeliverablesTab /> },
    { path: "logs", element: <LogsTab /> },
    { path: "live", element: <LiveTab /> },
  ]},
]);
