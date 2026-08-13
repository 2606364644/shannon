import { useEffect, useState } from "react";
import { useParams, useOutletContext } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { apiGetText, getScan, scanReportPath, blackboxRunReportPath } from "../../api/client";
// useTranslation 在子组件 SingleReport/CombinedReport 内使用；顶层 ReportTab 仅路由态。
import { MarkdownView } from "../../components/MarkdownView";
import { ErrorState } from "../../components/ErrorState";
import { Empty } from "../../components/Empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { RunFailureBanner, isRunFailureStatus } from "./runStatus";
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
  const [combined, setCombined] = useState<boolean | null>(null);

  // 先探 combined 标记（getScan 透传 session.combined）。失败/非组合 → null（走单视图）。
  useEffect(() => {
    if (!workspace || !scanId) return;
    setCombined(null);
    getScan(workspace, scanId)
      .then((s) => setCombined(s.combined === true))
      .catch(() => setCombined(false));
  }, [workspace, scanId]);

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

/** 非组合：原单报告视图（零回归）。 */
function SingleReport({ ws, scanId }: { ws: string; scanId: string }) {
  const { t } = useTranslation();
  const [md, setMd] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    setLoading(true);
    setErr(null);
    setMd("");
    apiGetText(scanReportPath(ws, scanId))
      .then((txt) => { setMd(txt); setLoading(false); })
      .catch((e: unknown) => { setErr(String(e)); setLoading(false); });
  }, [ws, scanId]);
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
  const [md, setMd] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    setErr(null);
    setMd("");
    const path = (selectedRun && (track === "blackbox" || track === "combined"))
      ? blackboxRunReportPath(ws, scanId, selectedRun, track === "combined" ? "combined" : undefined)
      : scanReportPath(ws, scanId, track);
    apiGetText(path)
      .then((txt) => { setMd(txt); setLoading(false); })
      .catch((e: unknown) => { setErr(String(e)); setLoading(false); });
  }, [ws, scanId, track, selectedRun]);

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
        <RunFailureBanner reason={runSummary!.reason} ws={ws} />
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
