import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Outlet, useParams, useLocation, useNavigate, Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/StatusBadge";
import { getScan } from "@/api/client";
import type { SessionData } from "@/api/types";
import { ErrorBoundary } from "@/components/ErrorBoundary";

// per-scan 视图的 tab 集：只含 scan 级 tab（overview/report/deliverables/logs/live）。
// repos/settings 是 ws 级，留在 ws 概览页入口，不进 scan tabs。
const SCAN_TABS = [
  { value: "overview", labelKey: "workspaceDetail.tabs.overview" },
  { value: "report", labelKey: "workspaceDetail.tabs.report" },
  { value: "deliverables", labelKey: "workspaceDetail.tabs.deliverables" },
  { value: "logs", labelKey: "workspaceDetail.tabs.logs" },
  { value: "live", labelKey: "workspaceDetail.tabs.live" },
] as const;

/**
 * per-scan 布局：scan header（scan_id + status + scan_type + 返回 ws）+ scan tabs + Outlet。
 * 数据源全 scan-scoped（getScan / scanReportPath / scanEventsUrl ...），见各 Tab 组件。
 * scan 操作（取消/删除/恢复/重跑）在 ws 概览页扫描卡片，此处只展示。
 */
export default function ScanDetail() {
  const { t } = useTranslation();
  const { workspace, scanId } = useParams<{ workspace: string; scanId: string }>();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  // 当前 tab = 路径末段（.../scans/:scanId/<tab>）。index 路由无 tab 段时由
  // DefaultScanTab 立即 replace 跳 live/report，此处 pop=scanId 为瞬时态（无高亮）。
  const current = pathname.split("/").pop() ?? "live";
  const [meta, setMeta] = useState<SessionData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!workspace || !scanId) return;
    setLoading(true);
    getScan(workspace, scanId)
      .then((s) => { setMeta(s); setLoading(false); })
      .catch(() => { setMeta(null); setLoading(false); });
  }, [workspace, scanId]);

  const status = meta?.status ?? meta?.session?.status ?? "running";

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Link
          to={`/p/${workspace}`}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-primary"
        >
          <ArrowLeft className="size-3.5" /> {t("workspaceDetail.backToWs", { ws: workspace })}
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-mono text-xl">{scanId}</h2>
          {loading ? (
            <Skeleton className="h-5 w-40" />
          ) : (
            <>
              <StatusBadge status={status} />
              {meta?.scan_type && (
                <Badge variant="outline" className="font-mono">{meta.scan_type}</Badge>
              )}
              {meta?.repo_path && (
                <span className="font-mono text-sm text-muted-foreground">{meta.repo_path}</span>
              )}
            </>
          )}
        </div>
      </div>
      <Tabs value={current} onValueChange={(v) => navigate(v)}>
        <div data-testid="scan-tabs-sticky" className="sticky top-12 z-30 print:static">
          <TabsList>
            {SCAN_TABS.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value}>{t(tab.labelKey)}</TabsTrigger>
            ))}
          </TabsList>
        </div>
      </Tabs>
      <div><ErrorBoundary key={current}><Outlet /></ErrorBoundary></div>
    </div>
  );
}
