import { useMemo } from "react";
import useSWR from "swr";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ApiError, getCorrelationDetail } from "@/api/client";
import { Empty } from "@/components/Empty";
import { ErrorState } from "@/components/ErrorState";
import { StatusBadge } from "@/components/StatusBadge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { isRunTerminal } from "./runStatus";
import { useScanDetail } from "./useScanDetail";
import { useScans } from "./useScans";

/** 三段接力各段的可视状态（复用组合扫描两段时间线的 segStatus i18n keys）。 */
export type CorrSegState = "inProgress" | "done" | "failed" | "pending" | "skipped";

const SEG_LABEL_KEY: Record<CorrSegState, string> = {
  inProgress: "workspaceDetail.scans.combined.segStatusInProgress",
  done: "workspaceDetail.scans.combined.segStatusDone",
  failed: "workspaceDetail.scans.combined.segStatusFailed",
  pending: "workspaceDetail.scans.combined.segStatusPending",
  skipped: "workspaceDetail.scans.combined.segStatusSkipped",
};

/** 段③黑盒验证 run 状态 → 段状态（对齐 runStatusLabelKey 的状态集）。 */
const RUN_SEG: Record<string, CorrSegState> = {
  running: "inProgress", "in-progress": "inProgress",
  completed: "done", done: "done",
  failed: "failed", crashed: "failed", killed: "failed",
  cancelled: "skipped", skipped: "skipped",
};

/**
 * 三段接力状态推导（纯函数，导出便于单测）：子仓白盒 → 跨仓关联 → 黑盒验证。
 *
 * 数据源限定 getScan + getCorrelationDetail（+ listScans 富化子仓状态，D4 同源约定），
 * 诚实局限：段①收尾与段②进行中在 topology 产出前无法从这两个 API 区分——段①子仓
 * 全终态且任务进行中时，段②统一取「进行中」（含 ①→② 的衔接瞬间）。
 *
 * - 段①：无子仓登记 → 待接力；无现扫（全复用）→ 已完成（复用即时满足）；现扫子仓
 *   任一非终态 → 进行中；全终态时任一失败 → 失败，否则已完成。子仓状态查不到
 *   （历史行被删）不参与判定，按任务级状态兜底。
 * - 段②：topology 已产出 → 已完成；主行失败 → 失败；段①就绪 + 任务进行中 → 进行中；
 *   其余 → 待接力。
 * - 段③：run 状态映射（cancelled/skipped → 已跳过）；无 run 且任务终态 → 已跳过
 *   （未配网关地址，验证段不执行）；否则待接力。
 */
export function deriveCorrelationSegments(input: {
  scanStatus: string;
  childrenCount: number;
  freshChildStatuses: (string | undefined)[];
  topologyReady: boolean;
  latestRunStatus?: string | null;
}): [CorrSegState, CorrSegState, CorrSegState] {
  const { scanStatus, childrenCount, freshChildStatuses, topologyReady, latestRunStatus } = input;
  const scanFailed = ["failed", "crashed", "killed"].includes(scanStatus);
  const scanTerminal = scanFailed
    || ["completed", "done", "cancelled"].includes(scanStatus);

  let seg1: CorrSegState;
  if (childrenCount === 0) {
    seg1 = "pending";
  } else if (freshChildStatuses.length === 0) {
    seg1 = "done"; // 全复用：提交即满足，无现扫子仓
  } else {
    const known = freshChildStatuses.filter((s): s is string => !!s);
    if (known.some((s) => !isRunTerminal(s))) seg1 = "inProgress";
    else if (known.length === 0) seg1 = scanTerminal ? "done" : "inProgress";
    else if (known.some((s) => ["failed", "crashed", "killed"].includes(s))) seg1 = "failed";
    else seg1 = "done";
  }

  let seg2: CorrSegState;
  if (topologyReady) seg2 = "done";
  else if (scanFailed) seg2 = "failed";
  else if (seg1 === "done" && !scanTerminal) seg2 = "inProgress";
  else seg2 = "pending";

  let seg3: CorrSegState;
  if (latestRunStatus != null && RUN_SEG[latestRunStatus]) seg3 = RUN_SEG[latestRunStatus];
  else if (latestRunStatus != null) seg3 = "pending"; // 未知 run 状态保守待接力
  else seg3 = scanTerminal ? "skipped" : "pending";

  return [seg1, seg2, seg3];
}

/**
 * 简版跨仓关联概览（D6，spec 2026-08-24 §8）：correlation 主行「概览」tab 内容——
 * 三段阶段横幅（子仓白盒 | 跨仓关联 | 黑盒验证）+ corr_children 子仓状态网格。
 * 主行 session 无常规 phases/agents metrics（OverviewTab 的瀑布/Agent 台账对关联行
 * 是空壳），由 OverviewTab 按 scan_type 整体切到本组件。
 *
 * 数据：getScan（useScanDetail 共享缓存）+ getCorrelationDetail（与 CorrelationTab
 * 共享 SWR key）+ listScans（子仓状态富化，NestedCorrChildren 同源约定）。
 * 完整结果视图在「跨仓关联」tab；此处 topology 就绪后给合并漏洞计数 + 跳转入口。
 */
export function CorrelationOverview({ ws, scanId }: { ws: string; scanId: string }) {
  const { t } = useTranslation();
  const { data: meta, loading: metaLoading, error: metaErr } = useScanDetail(ws, scanId);
  const { data: corr, error: corrErr, isLoading: corrLoading } = useSWR(
    ws && scanId ? ["corr-detail", ws, scanId] : null,
    () => getCorrelationDetail(ws, scanId),
    { refreshInterval: 15000 },
  );
  const { scans } = useScans(ws || undefined);
  const scansById = useMemo(() => new Map(scans.map((s) => [s.scan_id, s])), [scans]);

  if (metaErr) {
    return <ErrorState message={t("workspaceDetail.overview.loadError", { error: metaErr })} />;
  }
  if (metaLoading || !meta) {
    return (
      <div className="space-y-2">
        {[0, 1, 2].map((i) => <Skeleton key={i} className="h-8 w-full" />)}
      </div>
    );
  }
  if (corrErr) {
    return (
      <div data-testid="corr-ov-error">
        <ErrorState message={t("scan.correlation.loadError", {
          error: corrErr instanceof ApiError ? `API ${corrErr.status}` : String(corrErr),
        })} />
      </div>
    );
  }

  const children = corr?.corr_children ?? [];
  // 现扫在前、复用殿后（NestedCorrChildren 同序约定）
  const ordered = [...children.filter((c) => !c.reused), ...children.filter((c) => c.reused)];
  const runs = meta.bb_runs ?? [];
  const latestRun =
    runs.find((r) => r.run_id === meta.latest_bb_run) ?? runs[runs.length - 1] ?? null;
  const segs = corr
    ? deriveCorrelationSegments({
        scanStatus: meta.status ?? meta.session?.status ?? "running",
        childrenCount: children.length,
        freshChildStatuses: children.filter((c) => !c.reused)
          .map((c) => scansById.get(c.scan_id)?.status),
        topologyReady: !!corr.topology,
        latestRunStatus: latestRun?.status ?? null,
      })
    : null;
  const mergedVulnCount = corr
    ? Object.values(corr.merged_vulns).reduce((n, v) => n + v.length, 0)
    : 0;

  const segRows: [string, string, CorrSegState | null][] = [
    ["corr-seg-children", t("scan.correlation.segChildren"), segs?.[0] ?? null],
    ["corr-seg-correlation", t("scan.correlation.segCorrelation"), segs?.[1] ?? null],
    ["corr-seg-verify", t("scan.correlation.segVerify"), segs?.[2] ?? null],
  ];

  return (
    <div className="space-y-4">
      {/* 三段阶段横幅（视觉对齐 CombinedDetailTimeline 的两段时间线） */}
      <div
        data-testid="corr-overview-segs"
        className="rounded-md border border-border bg-card p-3 space-y-2"
      >
        <div className="text-xs font-medium text-muted-foreground">
          {t("scan.correlation.overviewPhaseTitle")}
        </div>
        {segRows.map(([testid, label, seg]) => (
          <div key={testid} data-testid={testid} className="flex items-center gap-2 text-sm">
            <span className="font-medium">{label}</span>
            <span className="text-xs text-muted-foreground">
              {seg ? t(SEG_LABEL_KEY[seg]) : "—"}
            </span>
          </div>
        ))}
      </div>

      {/* 子仓状态网格：corr_children + listScans 富化（查不到 → 弱「—」占位，D4 约定） */}
      <div data-testid="corr-ov-children" className="rounded-md border border-border bg-card overflow-hidden">
        <div className="border-b border-border px-4 py-2.5 text-sm font-semibold">
          {t("scan.correlation.childrenTitle")}
        </div>
        {corrLoading || !corr ? (
          <div className="space-y-2 p-4">
            {[0, 1].map((i) => <Skeleton key={i} className="h-6 w-full" />)}
          </div>
        ) : children.length === 0 ? (
          <Empty
            title={t("scan.correlation.overviewNoChildren")}
            hint={t("scan.correlation.overviewNoChildrenHint")}
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("scan.correlation.colService")}</TableHead>
                <TableHead>{t("scan.correlation.overviewColScan")}</TableHead>
                <TableHead>{t("scan.correlation.overviewColSource")}</TableHead>
                <TableHead>{t("scan.correlation.overviewColStatus")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {ordered.map((c) => {
                const s = scansById.get(c.scan_id);
                return (
                  <TableRow key={`${c.service}:${c.scan_id}`} data-testid={`corr-ov-child-${c.scan_id}`}>
                    <TableCell className="font-mono text-xs">{c.service}</TableCell>
                    <TableCell className="max-w-0 py-1.5">
                      <Link
                        to={`/p/${ws}/scans/${c.scan_id}`}
                        className="truncate font-mono text-xs hover:text-primary"
                      >
                        {s?.workflow_id ?? c.scan_id}
                      </Link>
                    </TableCell>
                    <TableCell className="text-xs">
                      {c.reused ? t("scan.correlation.childReused") : t("scan.correlation.childFresh")}
                    </TableCell>
                    <TableCell className="py-1.5">
                      {s ? <StatusBadge status={s.status} /> : <span className="text-muted-foreground/50">—</span>}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </div>

      {/* 关联产物就绪：合并漏洞计数 + 跳转完整结果视图 */}
      {corr?.topology && (
        <div
          data-testid="corr-ov-result"
          className="flex items-center gap-3 rounded-md border border-border bg-card p-3 text-sm"
        >
          <span>{t("scan.correlation.overviewMergedVulns", { count: mergedVulnCount })}</span>
          <Link
            to={`/p/${ws}/scans/${scanId}/correlation`}
            className="text-xs font-medium text-primary hover:underline"
          >
            {t("scan.correlation.overviewGoResult")} →
          </Link>
        </div>
      )}
    </div>
  );
}
