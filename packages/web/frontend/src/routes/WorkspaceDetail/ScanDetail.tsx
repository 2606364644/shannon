import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Outlet, useParams, useLocation, useNavigate, Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/StatusBadge";
import { WorkspaceSwitcher } from "@/components/WorkspaceSwitcher";
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

  // live/logs tab：根容器走 flex 链，高度 = 视口 - 固定的 TopBar(h-12=3rem) + main(py-5=2.5rem) = 5.5rem
  // （这俩不换行、精确可靠，非对 header 的估值）；header/tabs 用 shrink-0 保持自然高（窄屏 flex-wrap 换行
  // 也由 flex 自动吸收），Outlet 容器 flex-1 min-h-0 吃剩余空间 -> tab 内容动态填满、不溢出视口、无外层滚动条。
  // 其余 tab（overview/report/deliverables）保持 space-y-4 流式（依赖 window 滚，如 ReportTab TOC scroll-spy）。
  const isFlexLayout = current === "live" || current === "logs";

  return (
    <div className={isFlexLayout ? "flex h-[calc(100dvh-5.5rem)] flex-col gap-4" : "space-y-4"}>
      <div className={`space-y-2${isFlexLayout ? " shrink-0" : ""}`}>
        <Link
          to={`/p/${workspace}`}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-primary"
        >
          <ArrowLeft className="size-3.5" /> {t("workspaceDetail.backToWs", { ws: workspace })}
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-mono text-xl">{meta?.workflow_id ?? scanId}</h2>
          <WorkspaceSwitcher currentWorkspace={workspace} />
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
        <div data-testid="scan-tabs-sticky" className={`sticky top-12 z-30 print:static${isFlexLayout ? " shrink-0" : ""}`}>
          <TabsList>
            {SCAN_TABS.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value}>{t(tab.labelKey)}</TabsTrigger>
            ))}
          </TabsList>
        </div>
      </Tabs>
      <div className={isFlexLayout ? "min-h-0 flex-1 overflow-hidden" : undefined}><ErrorBoundary key={current}><Outlet /></ErrorBoundary></div>
    </div>
  );
}
