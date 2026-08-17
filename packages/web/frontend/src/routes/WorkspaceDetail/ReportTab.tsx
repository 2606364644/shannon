import { useState } from "react";
import { useParams, useOutletContext } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { scanReportPath, blackboxRunReportPath } from "../../api/client";
// useTranslation 在子组件 SingleReport/CombinedReport 内使用；顶层 ReportTab 仅路由态。
import { MarkdownView } from "../../components/MarkdownView";
import { ErrorState } from "../../components/ErrorState";
import { Empty } from "../../components/Empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { RunFailureBanner, isRunFailureStatus } from "./runStatus";
import { useScanDetail } from "./useScanDetail";
import { useApiText } from "@/api/useApiResource";
import type { BlackboxRunSummary } from "@/api/types";

type Track = "whitebox" | "blackbox" | "combined";

/**
 * 报告 tab。
 * 非组合（combined!=true）：原单报告视图（GET /report auto-infer）——零回归。
 * 组合（combined=true，spec §10.1 三视图）：渲染 [白盒报告 | 黑盒报告 | 融合报告] 子 tab，
 *   各拉 scanReportPath(ws, id, track)（?track=whitebox/blackbox/combined）。
 */
export function ReportTab() {
  const { workspace, scanId } = useParams<{ workspace: string; scanId: string }>();
  // combined 探测走共享 key（2026-08-17 批次 Task 2）：与 ScanDetail / OverviewTab 同份
  // ["scan", ws, id] 缓存，不再独立 getScan。失败 → false（走单视图）。
  const { data, error } = useScanDetail(workspace, scanId);
  const combined = error ? false : data ? data.combined === true : null;

  if (combined === null) {
    // combined 探测中：Skeleton（与单视图 loading 一致外观）。
    return (
      <div className="space-y-2">
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-6 w-full" />)}
      </div>
    );
  }
  if (combined) return <CombinedReport ws={workspace!} scanId={scanId!} />;
  return <SingleReport ws={workspace!} scanId={scanId!} />;
}

/** 非组合：原单报告视图（零回归；SWR 迁移 2026-08-17 批次 Task 4）。 */
function SingleReport({ ws, scanId }: { ws: string; scanId: string }) {
  const { t } = useTranslation();
  // key 即报告 path → 切 tab 重挂载时缓存即时显示（大 markdown 不重拉）。
  const { text: md, loading, error: err } = useApiText(scanReportPath(ws, scanId));
  if (err) return <ErrorState message={t("workspaceDetail.report.loadError", { error: err })} />;
  if (loading) {
    return (
      <div className="space-y-2">
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-6 w-full" />)}
      </div>
    );
  }
  if (!md) return <Empty title={t("workspaceDetail.report.emptyTitle")} hint={t("workspaceDetail.report.emptyHint")} />;
  return (
    // 报告是长文档型页面：外壳满宽（控制台风格）后，正文需可读字宽护栏，否则 prose max-w-none
    // 会铺到 ~2300px 行太长。max-w-5xl(1024px) 居中 = 文档阅读标准做法，与 live/logs 满宽控制台
    // 形成有意的对比。scan header/tabs 仍满宽（在 ReportTab 之外的 ScanDetail 层）。
    <div className="mx-auto max-w-5xl rounded-md border border-border bg-card p-4">
      <MarkdownView markdown={md} />
    </div>
  );
}

/** 组合：三子 tab，各拉对应 track 报告。黑盒/融合子 tab 按 selectedRun（版本化 run，spec
 * 2026-08-14）切到该 run 的 blackbox-runs/run-K 报告；白盒子 tab 仍 scan 级（共享）。 */
function CombinedReport({ ws, scanId }: { ws: string; scanId: string }) {
  const { t } = useTranslation();
  const outletCtx = useOutletContext<{ selectedRun?: string | null; runSummary?: BlackboxRunSummary | null }>();
  const selectedRun = outletCtx?.selectedRun ?? null;
  const runSummary = outletCtx?.runSummary ?? null;
  const [track, setTrack] = useState<Track>("combined");
  // key 即报告 path（track × selectedRun 派生）→ 三子 tab 切换后切回缓存即时显示。
  const path = (selectedRun && (track === "blackbox" || track === "combined"))
    ? blackboxRunReportPath(ws, scanId, selectedRun, track === "combined" ? "combined" : undefined)
    : scanReportPath(ws, scanId, track);
  const { text: md, loading, error: err } = useApiText(path);

  // 选中 run 终态失败且无可用报告 → 黑盒/融合子 tab 优先展示失败原因横幅（而非通用 Empty/Error）。
  const showRunFailure = (track === "blackbox" || track === "combined")
    && !!runSummary && isRunFailureStatus(runSummary.status) && !!runSummary.reason;

  return (
    // 同 SingleReport：组合报告三视图 + 正文统一收进可读字宽列（max-w-5xl 居中）。
    <div className="mx-auto max-w-5xl space-y-3">
      <Tabs value={track} onValueChange={(v) => setTrack(v as Track)}>
        <TabsList>
          <TabsTrigger value="whitebox">{t("workspaceDetail.report.combined.tabWhitebox")}</TabsTrigger>
          <TabsTrigger value="blackbox">{t("workspaceDetail.report.combined.tabBlackbox")}</TabsTrigger>
          <TabsTrigger value="combined">{t("workspaceDetail.report.combined.tabCombined")}</TabsTrigger>
        </TabsList>
      </Tabs>
      {showRunFailure ? (
        <RunFailureBanner reason={runSummary!.reason} ws={ws} detail={runSummary!.bb_failure_detail} />
      ) : err ? (
        <ErrorState message={t("workspaceDetail.report.loadError", { error: err })} />
      ) : loading ? (
        <div className="space-y-2">
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-6 w-full" />)}
        </div>
      ) : !md ? (
        <Empty title={t("workspaceDetail.report.emptyTitle")} hint={t("workspaceDetail.report.emptyHint")} />
      ) : (
        <div className="rounded-md border border-border bg-card p-4">
          <MarkdownView markdown={md} />
        </div>
      )}
    </div>
  );
}
