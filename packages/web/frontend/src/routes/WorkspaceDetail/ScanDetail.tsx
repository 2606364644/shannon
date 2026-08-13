import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Outlet, useParams, useLocation, useNavigate, Link, useSearchParams } from "react-router-dom";
import { ArrowLeft, RefreshCw, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { StatusBadge } from "@/components/StatusBadge";
import { getScan, rerunBlackbox, addBlackboxToWhitebox, deleteBlackboxRun, ApiError } from "@/api/client";
import type { SessionData, BlackboxRunSummary } from "@/api/types";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { runStatusLabelKey, isRunFailureStatus, isRunTerminal, RunFailureBanner } from "./runStatus";
import { ScanProgressOverview } from "./ScanProgressOverview";

// per-scan 视图的 tab 集：只含 scan 级 tab（overview/report/deliverables/logs/live）。
// repos/settings 是 ws 级，留在 ws 概览页入口，不进 scan tabs。
const SCAN_TABS = [
  { value: "overview", labelKey: "workspaceDetail.tabs.overview" },
  { value: "report", labelKey: "workspaceDetail.tabs.report" },
  { value: "deliverables", labelKey: "workspaceDetail.tabs.deliverables" },
  { value: "logs", labelKey: "workspaceDetail.tabs.logs" },
  { value: "live", labelKey: "workspaceDetail.tabs.live" },
] as const;

// === 组合扫描段状态时间线（spec 2026-08-12 §9 / §11.3）===
// 详情页：白盒段 + 黑盒段两个时间段的状态（靠 bb_phase 切段），黑盒 failed 时附「续扫黑盒」。
// 步级 / Agent 实时进度在顶部 ScanProgressOverview（全 tab 常驻），此处不再重复渲染步级。
// 纯白盒/纯黑盒（combined!=true）不渲染此组件——零回归。

function segStatusKey(bbPhase: string | null | undefined, segment: "whitebox" | "blackbox"): string {
  // 白盒段：precheck/pending 进行中；之后（黑盒已起或跳过）= 已完成。
  // 黑盒段：直接映射 bb_phase；未到 running 为待接力。
  if (segment === "whitebox") {
    return bbPhase === "precheck" || bbPhase === "pending"
      ? "workspaceDetail.scans.combined.segStatusInProgress"
      : "workspaceDetail.scans.combined.segStatusDone";
  }
  switch (bbPhase) {
    case "running": return "workspaceDetail.scans.combined.segStatusInProgress";
    case "completed": return "workspaceDetail.scans.combined.segStatusDone";
    case "failed": return "workspaceDetail.scans.combined.segStatusFailed";
    case "skipped": return "workspaceDetail.scans.combined.segStatusSkipped";
    default: return "workspaceDetail.scans.combined.segStatusPending"; // precheck/pending/unknown
  }
}

function CombinedDetailTimeline({
  ws, scanId, bbPhase, onRerunDone,
}: { ws: string; scanId: string; bbPhase?: string | null; onRerunDone: () => void }) {
  const { t } = useTranslation();
  const [showRerun, setShowRerun] = useState(false);
  const [busy, setBusy] = useState(false);

  const blackboxFailed = bbPhase === "failed";

  async function doRerun() {
    setBusy(true);
    try {
      await rerunBlackbox(ws, scanId);
      toast.success(t("workspaceDetail.scans.combined.rerunSuccess"));
      setShowRerun(false);
      onRerunDone(); // 刷新 summary：bb_phase failed → running
    } catch (e) {
      toast.error(t("workspaceDetail.scans.combined.rerunFailed", {
        error: e instanceof ApiError ? String(e.status) : e instanceof Error ? e.message : String(e),
      }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-md border border-border bg-card p-3 space-y-2">
      <div className="text-xs font-medium text-muted-foreground">
        {t("workspaceDetail.scans.combined.detailTimelineTitle")}
      </div>
      {/* 白盒段 */}
      <div className="flex items-center gap-2 text-sm" data-testid="combined-segment-whitebox">
        <span className="font-medium">{t("workspaceDetail.scans.combined.segmentWhitebox")}</span>
        <span className="text-xs text-muted-foreground">{t(segStatusKey(bbPhase, "whitebox"))}</span>
      </div>
      {/* 黑盒段（+ 失败时续扫按钮）*/}
      <div className="flex items-center gap-2 text-sm" data-testid="combined-segment-blackbox">
        <span className="font-medium">{t("workspaceDetail.scans.combined.segmentBlackbox")}</span>
        <span className="text-xs text-muted-foreground">{t(segStatusKey(bbPhase, "blackbox"))}</span>
        {blackboxFailed && (
          <Button size="sm" variant="outline" onClick={() => setShowRerun(true)} disabled={busy}>
            <RefreshCw className="size-3.5" /> {t("workspaceDetail.scans.combined.rerunBlackbox")}
          </Button>
        )}
      </div>
      {/* 续扫确认弹窗（simple confirm + POST，沿用原认证，spec §11.3 v1）*/}
      <Dialog open={showRerun} onOpenChange={(o) => !o && setShowRerun(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("workspaceDetail.scans.combined.rerunConfirmTitle")}</DialogTitle>
            <DialogDescription>{t("workspaceDetail.scans.combined.rerunConfirmDesc")}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setShowRerun(false)}>{t("common.cancel")}</Button>
            <Button onClick={doRerun} disabled={busy}>{t("common.confirm")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/**
 * per-scan 布局：scan header（scan_id + status + scan_type + 返回 ws）+ scan tabs + Outlet。
 * 数据源全 scan-scoped（getScan / scanReportPath / scanEventsUrl ...），见各 Tab 组件。
 * scan 操作（取消/删除/恢复/重跑）在 ws 概览页扫描卡片，此处只展示。
 * 组合扫描（combined=true）：header 下渲染两段时间线 + 黑盒失败续跑入口。
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

  const load = () => {
    if (!workspace || !scanId) return;
    setLoading(true);
    getScan(workspace, scanId)
      .then((s) => { setMeta(s); setLoading(false); })
      .catch(() => { setMeta(null); setLoading(false); });
  };
  useEffect(load, [workspace, scanId]);

  const status = meta?.status ?? meta?.session?.status ?? "running";
  const isCombined = meta?.combined === true;
  // 版本化黑盒 run（spec 2026-08-14 §5.2）：?run= 选中（默认 latest_bb_run），供 ReportTab/
  // DeliverablesTab 按 run 切黑盒/融合产物。终端态白盒任务提供「加黑盒」入口（建下一个 run）。
  const [searchParams, setSearchParams] = useSearchParams();
  const runs = meta?.bb_runs ?? [];
  const selectedRun = searchParams.get("run") ?? meta?.latest_bb_run ?? null;
  const selectedRunObj: BlackboxRunSummary | null =
    runs.find((r) => r.run_id === selectedRun) ?? null;
  const [addBbOpen, setAddBbOpen] = useState(false);
  const [addBbBusy, setAddBbBusy] = useState(false);
  const [deleteRunOpen, setDeleteRunOpen] = useState(false);
  const [deleteRunBusy, setDeleteRunBusy] = useState(false);
  const whiteboxTerminal =
    meta?.scan_type === "whitebox" && ["completed", "done"].includes(status);

  const submitAddBlackbox = async () => {
    if (!workspace || !scanId) return;
    setAddBbBusy(true);
    try {
      const r = await addBlackboxToWhitebox(workspace, scanId, {});
      toast.success(t("workspaceDetail.scans.runs.addedSuccess"));
      setAddBbOpen(false);
      setSearchParams({ run: r.run_id });
      load();
    } catch (e) {
      toast.error(t("workspaceDetail.scans.runs.addedFailed", { error: (e as Error).message }));
    } finally {
      setAddBbBusy(false);
    }
  };

  // 删除当前选中 run（spec §7.1 #4）：终态可删，运行中禁用（后端 409 兜底）。
  // 删的即当前选中 → 回退 run 选择（清 ?run=，load 后 selectedRun 回落到 latest_bb_run）。
  const submitDeleteRun = async () => {
    if (!workspace || !scanId || !selectedRun) return;
    setDeleteRunBusy(true);
    try {
      await deleteBlackboxRun(workspace, scanId, selectedRun);
      toast.success(t("workspaceDetail.scans.runs.deleted", { runId: selectedRun }));
      setDeleteRunOpen(false);
      setSearchParams((prev) => { prev.delete("run"); return prev; });
      load();
    } catch (e) {
      toast.error(t("workspaceDetail.scans.runs.deleteFailed", {
        error: e instanceof ApiError ? String(e.status) : e instanceof Error ? e.message : String(e),
      }));
    } finally {
      setDeleteRunBusy(false);
    }
  };

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
          {/* 版本化黑盒 run 选择器（spec 2026-08-14 §5.2）：组合任务多 run 时切换查看。 */}
          {isCombined && runs.length > 0 && (
            <select
              aria-label={t("workspaceDetail.scans.runs.select")}
              className="h-8 rounded-md border border-input bg-background px-2 text-sm"
              value={selectedRun ?? ""}
              onChange={(e) => setSearchParams(e.target.value ? { run: e.target.value } : {})}
            >
              {runs.map((r) => {
                const labelKey = runStatusLabelKey(r.status);
                // status 后缀优先于「最新」：终态/运行中状态比 latest 标记更有信息量。
                const suffix = labelKey
                  ? ` · ${t(labelKey)}`
                  : r.run_id === meta?.latest_bb_run
                    ? ` · ${t("workspaceDetail.scans.runs.latest")}`
                    : "";
                return (
                  <option key={r.run_id} value={r.run_id}>
                    {r.run_id}{suffix}
                  </option>
                );
              })}
            </select>
          )}
          {/* 删除选中 run（spec §7.1 #4）：仅终态可删，运行中禁用（后端 409 兜底）。 */}
          {isCombined && runs.length > 0 && selectedRun && selectedRunObj && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setDeleteRunOpen(true)}
              disabled={deleteRunBusy || !isRunTerminal(selectedRunObj.status)}
              title={!isRunTerminal(selectedRunObj.status)
                ? t("workspaceDetail.scans.runs.deleteRunningHint") : undefined}
            >
              <Trash2 className="size-3.5" /> {t("workspaceDetail.scans.runs.delete")}
            </Button>
          )}
          {/* 加黑盒入口（spec §6）：终端态白盒任务可新建黑盒 run（纯白盒→首个 run；
              已 combined→下一个 run）。 */}
          {whiteboxTerminal && (
            <Button size="sm" variant="outline" onClick={() => setAddBbOpen(true)}>
              <Plus className="size-3.5" /> {t("workspaceDetail.scans.runs.addBlackbox")}
            </Button>
          )}
        </div>
      </div>
      {/* 顶部常驻进度概览（所有 tab 可见，所有扫描类型）：当前阶段 + 步级 + 正在跑的 Agent
          （spec 进度两层粒度 · 详情页细粒度）。组合黑盒段自动读选中 run 的 events。 */}
      {!loading && meta && (
        <div className={isFlexLayout ? "shrink-0" : ""}>
          <ScanProgressOverview
            ws={workspace!} scanId={scanId!}
            combined={meta.combined} bbPhase={meta.bb_phase} selectedRun={selectedRun}
          />
        </div>
      )}
      {/* 组合扫描两段时间线（spec §9/§11.3）：combined 时渲染；shrink-0 以兼容 live/logs flex 链。 */}
      {isCombined && !loading && (
        <div className={isFlexLayout ? "shrink-0" : ""}>
          <CombinedDetailTimeline
            ws={workspace!} scanId={scanId!} bbPhase={meta?.bb_phase} onRerunDone={load}
          />
        </div>
      )}
      {/* 选中 run 失败/跳过且有原因 → 顶部展示可读失败横幅 + 引导（spec 2026-08-14 可见性）。
          后端 run session 的 bb_reason / 任务 bb_runs[].reason 经 API 透传到此。 */}
      {isCombined && selectedRunObj && isRunFailureStatus(selectedRunObj.status) &&
        selectedRunObj.reason && (
        <div className={isFlexLayout ? "shrink-0" : ""}>
          <RunFailureBanner reason={selectedRunObj.reason} ws={workspace ?? undefined} />
        </div>
      )}
      <Tabs value={current} onValueChange={(v) => navigate(v)}>
        <div data-testid="scan-tabs-sticky" className={`sticky top-12 z-30 print:static${isFlexLayout ? " shrink-0" : ""}`}>
          <TabsList>
            {SCAN_TABS.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value}>{t(tab.labelKey)}</TabsTrigger>
            ))}
          </TabsList>
        </div>
      </Tabs>
      <div className={isFlexLayout ? "min-h-0 flex-1 overflow-hidden" : undefined}><ErrorBoundary key={current}><Outlet context={{ selectedRun, runSummary: selectedRunObj }} /></ErrorBoundary></div>

      {/* 加黑盒确认 Dialog（空 body = 无认证直连；后续可扩认证/HOST 选择） */}
      <Dialog open={addBbOpen} onOpenChange={setAddBbOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("workspaceDetail.scans.runs.addBlackbox")}</DialogTitle>
            <DialogDescription>{t("workspaceDetail.scans.runs.addBlackboxDesc")}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setAddBbOpen(false)} disabled={addBbBusy}>
              {t("common.cancel")}
            </Button>
            <Button onClick={submitAddBlackbox} disabled={addBbBusy}>
              {addBbBusy && <RefreshCw className="mr-1 size-3.5 animate-spin" />}
              {t("common.confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {/* 删除 run 确认 Dialog */}
      <Dialog open={deleteRunOpen} onOpenChange={setDeleteRunOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("workspaceDetail.scans.runs.deleteConfirmTitle")}</DialogTitle>
            <DialogDescription>
              {t("workspaceDetail.scans.runs.deleteConfirmDesc", { runId: selectedRun })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDeleteRunOpen(false)} disabled={deleteRunBusy}>
              {t("common.cancel")}
            </Button>
            <Button variant="destructive" onClick={submitDeleteRun} disabled={deleteRunBusy}>
              {deleteRunBusy && <RefreshCw className="mr-1 size-3.5 animate-spin" />}
              {t("common.confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
