import { useState } from "react";
import { useParams, useOutletContext } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Download } from "lucide-react";
import {
  scanReportPath, blackboxRunReportPath,
  scanReportDataPath, blackboxRunReportDataPath, apiGetText,
} from "../../api/client";
// useTranslation 在子组件 SingleReport/CombinedReport 内使用；顶层 ReportTab 仅路由态。
import { MarkdownView } from "../../components/MarkdownView";
import { ErrorState } from "../../components/ErrorState";
import { Empty } from "../../components/Empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { RunFailureBanner, isRunFailureStatus } from "./runStatus";
import { useScanDetail } from "./useScanDetail";
import { useApiText, useReportData } from "@/api/useApiResource";
import { downloadTextFile, reportDownloadFilename } from "@/lib/download";
import { ReportView } from "@/components/report/ReportView";
import type { BlackboxRunSummary, ReportData } from "@/api/types";

type Track = "whitebox" | "blackbox" | "combined";

/** 报告页版心（布局不变量）：报告是长文档型页面，卡片边框/表格/散文/POC 须共享同
 *  一居中列宽，布局节奏才稳定——2026-08-26 满宽实验（b1cf3fb3 删版心 + 护栏下沉
 *  RichText 768px）反例：散文 768px vs 卡片满宽 → 左重右空、表格列距拉稀，已回滚。
 *  档位 1536px：endpoints 表 7 列 mono（Path/Source/Sink 等 file:line）自然需求
 *  ~1300px+，1280(7xl) 下挤、1536 舒展——整页单一版心提档，不做表格单独满宽（单一
 *  宽度 > 局部护栏）。控制台型页（live/logs/产物）满宽是另一立场，勿混同。 */
const REPORT_COL_CLS = "mx-auto w-full max-w-[1536px]";

/** 报告卡右上「下载 .md」：直接落已在内存的 md 全文（/report 无截断），不重发请求。 */
function ReportDownloadButton({ filename, md }: { filename: string; md: string }) {
  const { t } = useTranslation();
  return (
    <Button
      variant="ghost"
      size="sm"
      className="text-muted-foreground"
      onClick={() => downloadTextFile(filename, md)}
    >
      <Download aria-hidden />
      {t("workspaceDetail.report.download")}
    </Button>
  );
}

/**
 * 结构化报告的「下载 .md」：JSON 渲染路径不在内存持 md 全文，点击时按需拉导出 md
 * （/report 端点语义收窄为 md 下载/预览，spec 2026-08-26 §7.1）再落盘；空 md（尚未
 * 导出）静默不动作。
 */
function AsyncReportDownloadButton({ filename, mdPath }: { filename: string; mdPath: string }) {
  const { t } = useTranslation();
  const onDownload = () => {
    apiGetText(mdPath)
      .then((md) => {
        if (md) downloadTextFile(filename, md);
      })
      .catch(() => {
        /* md 导出不可用时不阻塞报告浏览（结构化视图已是主呈现） */
      });
  };
  return (
    <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={onDownload}>
      <Download aria-hidden />
      {t("workspaceDetail.report.download")}
    </Button>
  );
}

function ReportSkeleton() {
  return (
    <div className="space-y-2">
      {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-6 w-full" />)}
    </div>
  );
}

/**
 * md 降级渲染分支（旧 scan 无 report_data.json → GET report-data 404）：保留原
 * 「拉 /report text/plain + MarkdownView」链路，不再投入开发（spec 2026-08-26 §7.2
 * 「旧 scan 回退」）。path 即 SWR key → 切 tab 重挂载缓存即时显示。
 */
function LegacyMdReport({ path, filename }: { path: string; filename: string }) {
  const { t } = useTranslation();
  const { text: md, loading, error: err } = useApiText(path);
  if (err) return <ErrorState message={t("workspaceDetail.report.loadError", { error: err })} />;
  if (loading) return <ReportSkeleton />;
  if (!md)
    return <Empty title={t("workspaceDetail.report.emptyTitle")} hint={t("workspaceDetail.report.emptyHint")} />;
  return (
    <div className="rounded-md border border-border bg-card p-4 [backdrop-filter:var(--backdrop-card,none)]">
      <div className="mb-2 flex justify-end">
        <ReportDownloadButton filename={filename} md={md} />
      </div>
      <MarkdownView markdown={md} />
    </div>
  );
}

/** 结构化渲染分支外壳：同旧布局（可读字宽 + 卡面），正文换 ReportView（纯渲染）。 */
function StructuredReportShell({
  data, mdPath, filename,
}: { data: ReportData; mdPath: string; filename: string }) {
  return (
    <div
      data-testid="structured-report"
      className="rounded-md border border-border bg-card p-4 [backdrop-filter:var(--backdrop-card,none)]"
    >
      <div className="mb-2 flex justify-end">
        <AsyncReportDownloadButton filename={filename} mdPath={mdPath} />
      </div>
      <ReportView data={data} />
    </div>
  );
}

/**
 * 报告 tab。
 * 非组合（combined!=true）：单报告视图——优先 GET /report-data（结构化 SSOT，
 * ReportView 纯渲染）；404（旧 scan）回退 md 渲染路径。
 * 组合（combined=true，spec §10.1 三视图）：渲染 [白盒报告 | 黑盒报告 | 融合报告] 子 tab，
 *   各 track 同样「report-data 优先 → md 回退」；黑盒/融合按 selectedRun 切 run 级端点。
 */
export function ReportTab() {
  const { workspace, scanId } = useParams<{ workspace: string; scanId: string }>();
  // combined 探测走共享 key（2026-08-17 批次 Task 2）：与 ScanDetail / OverviewTab 同份
  // ["scan", ws, id] 缓存，不再独立 getScan。失败 → false（走单视图）。
  const { data, error } = useScanDetail(workspace, scanId);
  const combined = error ? false : data ? data.combined === true : null;

  if (combined === null) {
    // combined 探测中：Skeleton（与单视图 loading 一致外观）。
    return <ReportSkeleton />;
  }
  // scan_type 供 report-data 定 track（blackbox 扫描读黑盒桶；其余按白盒）。
  const scanType = data?.scan_type ?? null;
  if (combined) return <CombinedReport ws={workspace!} scanId={scanId!} scanType={scanType} />;
  return <SingleReport ws={workspace!} scanId={scanId!} scanType={scanType} />;
}

/** 非组合：单报告视图（report-data 优先，md 降级）。 */
function SingleReport({ ws, scanId, scanType }: { ws: string; scanId: string; scanType: string | null }) {
  const { t } = useTranslation();
  const track: "whitebox" | "blackbox" = scanType === "blackbox" ? "blackbox" : "whitebox";
  // key 即 path → 切 tab 重挂载时缓存即时显示。
  const rd = useReportData(scanReportDataPath(ws, scanId, track));
  // md 降级路径与旧行为一致：不传 track 走 backend auto-infer（零回归）。
  const mdPath = scanReportPath(ws, scanId);
  const filename = reportDownloadFilename(scanId);

  let body;
  if (rd.loading) {
    body = <ReportSkeleton />;
  } else if (rd.data) {
    body = <StructuredReportShell data={rd.data} mdPath={mdPath} filename={filename} />;
  } else if (rd.notFound) {
    body = <LegacyMdReport path={mdPath} filename={filename} />;
  } else {
    body = <ErrorState message={t("workspaceDetail.report.loadError", { error: rd.error })} />;
  }
  // 版心列（REPORT_COL_CLS）：所有分支共享——拉宽提档（7xl）但布局结构不变，见常量注释。
  return (
    <div data-testid="report-page-column" className={REPORT_COL_CLS}>
      {body}
    </div>
  );
}

/** 组合：三子 tab，各拉对应 track。黑盒/融合子 tab 按 selectedRun（版本化 run，spec
 * 2026-08-14）切到该 run 的 blackbox-runs/run-K 端点；白盒子 tab 仍 scan 级（共享）。 */
function CombinedReport({ ws, scanId, scanType }: { ws: string; scanId: string; scanType: string | null }) {
  const { t } = useTranslation();
  const outletCtx = useOutletContext<{ selectedRun?: string | null; runSummary?: BlackboxRunSummary | null }>();
  const selectedRun = outletCtx?.selectedRun ?? null;
  const runSummary = outletCtx?.runSummary ?? null;
  const [track, setTrack] = useState<Track>("combined");

  // md 路径（下载/降级渲染）与 report-data 路径（结构化优先）同条件派生（同一 selectedRun 门控）。
  const isRunTrack = !!selectedRun && (track === "blackbox" || track === "combined");
  const mdPath = isRunTrack
    ? blackboxRunReportPath(ws, scanId, selectedRun!, track === "combined" ? "combined" : undefined)
    : scanReportPath(ws, scanId, track);
  // scan 级无 track=combined 的 report-data（融合产物 per-run）→ selectedRun 缺席的
  // combined 子 tab 直接走 md 降级分支（structuredPath=null 挂起 SWR）。
  const structuredPath = isRunTrack
    ? blackboxRunReportDataPath(ws, scanId, selectedRun!, track === "combined" ? "combined" : "blackbox")
    : track === "combined"
      ? null
      : scanReportDataPath(ws, scanId, track === "blackbox" || scanType === "blackbox" ? "blackbox" : "whitebox");
  const rd = useReportData(structuredPath);
  // 下载文件名的 run 段与 path 派生同条件（同一 selectedRun 门控）。
  const runId = isRunTrack ? selectedRun : null;
  const filename = reportDownloadFilename(scanId, track, runId);

  // 选中 run 终态失败且无可用报告 → 黑盒/融合子 tab 优先展示失败原因横幅（而非通用 Empty/Error）。
  const showRunFailure = (track === "blackbox" || track === "combined")
    && !!runSummary && isRunFailureStatus(runSummary.status) && !!runSummary.reason;

  let body;
  if (rd.loading) {
    body = <ReportSkeleton />;
  } else if (rd.data) {
    body = <StructuredReportShell data={rd.data} mdPath={mdPath} filename={filename} />;
  } else if (structuredPath && !rd.notFound) {
    body = <ErrorState message={t("workspaceDetail.report.loadError", { error: rd.error })} />;
  } else {
    // 404（旧 scan）或 scan 级 combined 无 run → md 渲染降级分支。
    body = <LegacyMdReport path={mdPath} filename={filename} />;
  }

  return (
    // 同 SingleReport：版心列（REPORT_COL_CLS）包 tab 条 + 正文，布局结构不变。
    <div data-testid="report-page-column" className={`${REPORT_COL_CLS} space-y-3`}>
      <Tabs value={track} onValueChange={(v) => setTrack(v as Track)}>
        <TabsList>
          <TabsTrigger value="whitebox">{t("workspaceDetail.report.combined.tabWhitebox")}</TabsTrigger>
          <TabsTrigger value="blackbox">{t("workspaceDetail.report.combined.tabBlackbox")}</TabsTrigger>
          <TabsTrigger value="combined">{t("workspaceDetail.report.combined.tabCombined")}</TabsTrigger>
        </TabsList>
      </Tabs>
      {showRunFailure ? (
        <RunFailureBanner reason={runSummary!.reason} ws={ws} detail={runSummary!.bb_failure_detail} />
      ) : (
        body
      )}
    </div>
  );
}
