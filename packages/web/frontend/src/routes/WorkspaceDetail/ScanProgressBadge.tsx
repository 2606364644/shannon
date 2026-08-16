import type { ReactElement } from "react";
import { useTranslation } from "react-i18next";
import type { ScanSummary } from "@/api/types";

/**
 * 工作区列表卡统一进度徽标（spec 2026-08-14 进度两层粒度 · 列表页粗粒度）。
 *
 * 所有**运行中**扫描卡显示：x%(progress_pct 后端预算) + 进度条 + 段标签。终态卡不渲染
 * （看 StatusBadge + 漏洞数即可）。段标签：
 * - 纯白盒 → 「白盒」+ 可选实时 phase 后缀（如「白盒 · recon」，phase 来自运行中卡 SSE）
 * - 纯黑盒 → 「黑盒」+ 可选实时 phase 后缀
 * - 组合扫描 → 按 bb_phase 映射（预验证中 / 白盒扫描中 / 黑盒扫描中），复用 combined.* key
 *
 * 取代原仅组合卡的 CombinedProgressBadge——列表页对所有类型统一粗粒度呈现。
 * 精确步级 / Agent 在扫描详情页顶部（细粒度层）。
 */
const RUNS = "workspaceDetail.scans";

// 终态集：终态卡不渲染进度（避免完成/失败卡显示一个静止条）。
const TERMINAL = new Set(["completed", "done", "failed", "killed", "crashed", "skipped"]);

// 组合扫描 bb_phase → 段标签 i18n key（复用 combined.* 现有 key）。
const BB_PHASE_LABEL_KEY: Record<string, string> = {
  precheck: `${RUNS}.combined.phasePrecheck`,
  pending: `${RUNS}.combined.phasePending`,
  running: `${RUNS}.combined.phaseRunning`,
};

/** 运行中扫描的段标签（组合按 bb_phase 映射；纯白盒/黑盒 = 段名 + 可选 SSE 实时 phase）。
 *  徽标（ScanProgressBadge）与 ScanList 表格进度格共用，避免两处口径漂移。 */
export function scanSegmentLabel(
  scan: Pick<ScanSummary, "combined" | "bb_phase" | "scan_type">,
  currentPhase: string | null | undefined,
  t: (key: string) => string,
): string {
  if (scan.combined === true) {
    const key = BB_PHASE_LABEL_KEY[scan.bb_phase ?? ""] ?? `${RUNS}.combined.phaseUnknown`;
    return t(key);
  }
  const track = scan.scan_type === "blackbox"
    ? t(`${RUNS}.progress.blackbox`)
    : t(`${RUNS}.progress.whitebox`);
  return currentPhase ? `${track} · ${currentPhase}` : track;
}

export interface ScanProgressBadgeProps {
  scan: ScanSummary;
  /** 运行中卡的实时当前阶段（来自 SSE 最后一条 PhaseEvent.phase），可选；纯白盒/纯黑盒段标签后缀。 */
  currentPhase?: string | null;
}

export function ScanProgressBadge({ scan, currentPhase }: ScanProgressBadgeProps): ReactElement | null {
  const { t } = useTranslation();
  // 仅运行中渲染进度；终态 + 非运行中（如 interrupted 待恢复）不渲染。
  if (TERMINAL.has(scan.status)) return null;
  if (!scan.is_running && scan.status !== "running") return null;

  const pct = Math.max(0, Math.min(100, Math.round(scan.progress_pct ?? 0)));

  const segmentText = scanSegmentLabel(scan, currentPhase, t);

  return (
    <span className="inline-flex items-center gap-2" data-testid="scan-progress">
      <span className="font-mono text-sm font-semibold leading-none">{pct}%</span>
      <span
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        className="inline-block h-1.5 w-24 overflow-hidden rounded-full bg-muted"
      >
        <span
          className="block h-full rounded-full bg-primary transition-all"
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className="text-xs text-muted-foreground whitespace-nowrap">{segmentText}</span>
    </span>
  );
}
