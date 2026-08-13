import type { ReactElement } from "react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import { scanEventsUrl, blackboxRunEventsUrl } from "@/api/client";
import { useEventSource } from "@/api/useEventSource";
import { dashboardReducer, emptyState } from "@/state/dashboardReducer";

/**
 * 详情页进度概览（spec 2026-08-14 进度两层粒度 · 详情页细粒度）。
 *
 * 顶部全 tab 常驻：当前阶段 + 步级列表 + 正在跑的 Agent + 进度计数 + 连接态。
 * 建单条 SSE（按段切换 eventsUrl：组合黑盒段读 run 级 ndjson，其余读任务根），events 经
 * dashboardReducer fold 成 DashboardState（1:1 复刻 core），渲染其 phase/agents/units 核心。
 * 精简于 DashboardPanel（不含耗时/花费指标——那些在 overview tab 的 metrics）。
 */

const BLACKBOX_PHASES = new Set(["running", "completed", "failed", "skipped"]);

/** 算当前活跃段的 SSE events URL（组合黑盒段 + 选中 run → run 级；其余 → 任务根）。 */
export function resolveActiveEventsUrl(opts: {
  ws: string;
  scanId: string;
  combined?: boolean | null;
  bbPhase?: string | null;
  selectedRun?: string | null;
}): string {
  const { ws, scanId, combined, bbPhase, selectedRun } = opts;
  if (combined === true && selectedRun && bbPhase && BLACKBOX_PHASES.has(bbPhase)) {
    return blackboxRunEventsUrl(ws, scanId, selectedRun);
  }
  return scanEventsUrl(ws, scanId);
}

// 连接态徽章（复用 live tab 的 status key）。
const STATUS_MAP: Record<string, { labelKey: string; cls: string }> = {
  open: { labelKey: "workspaceDetail.live.statusOpen", cls: "border-cyan/40 text-cyan" },
  error: { labelKey: "workspaceDetail.live.statusError", cls: "border-yellow/40 text-yellow" },
  closed: { labelKey: "workspaceDetail.live.statusClosed", cls: "border-muted-foreground/40 text-muted-foreground" },
};

const UNIT_STATUS_CLS: Record<string, string> = {
  running: "text-cyan",
  done: "text-green",
  failed: "text-red",
};

export interface ScanProgressOverviewProps {
  ws: string;
  scanId: string;
  combined?: boolean | null;
  bbPhase?: string | null;
  selectedRun?: string | null;
}

export function ScanProgressOverview({
  ws, scanId, combined, bbPhase, selectedRun,
}: ScanProgressOverviewProps): ReactElement {
  const { t } = useTranslation();
  const eventsUrl = resolveActiveEventsUrl({ ws, scanId, combined, bbPhase, selectedRun });
  const { events, status } = useEventSource(eventsUrl);
  const state = useMemo(() => events.reduce(dashboardReducer, emptyState()), [events]);
  const running = Object.values(state.agents).filter((a) => a.status === "running");
  const agentTotal = Object.keys(state.agents).length;
  const showProgress = state.total_units > 0;
  const sm = STATUS_MAP[status] ?? STATUS_MAP.closed;

  return (
    <div
      className="rounded-md border border-border bg-card p-3 shadow-[var(--shadow-card)]"
      data-testid="scan-progress-overview"
    >
      {/* 头条：当前阶段（coral 主角）+ 连接态徽章 */}
      <div className="flex items-start justify-between gap-3">
        <span className="font-sans text-base font-semibold leading-tight text-primary">
          {state.current_phase ?? "—"}
        </span>
        <Badge variant="outline" className={`gap-1 ${sm.cls}`}>
          <span aria-hidden>●</span>{t(sm.labelKey)}
        </Badge>
      </div>

      {/* 进度计数副标题：步级 completed/total · Agent completed/total */}
      {(showProgress || agentTotal > 0) && (
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono text-xs text-muted-foreground">
          {showProgress && <span>{t("dashboard.progress")} {state.completed_units}/{state.total_units}</span>}
          {showProgress && agentTotal > 0 && <span aria-hidden className="opacity-40">·</span>}
          {agentTotal > 0 && <span>Agent {state.completed_count}/{agentTotal}</span>}
        </div>
      )}

      {/* 当前阶段的步级列表（✓/○/✗ + intent） */}
      {state.phase_units.length > 0 && (
        <div className="mt-2 space-y-0.5 text-xs">
          {state.phase_units.map((unit) => {
            const st = state.unit_status[unit];
            return (
              <div key={unit} className="flex gap-2">
                <span className={UNIT_STATUS_CLS[st ?? ""] ?? "text-muted-foreground"}>
                  {st === "running" ? "○" : st === "done" ? "✓" : st === "failed" ? "✗" : "·"}
                </span>
                <span className="text-foreground">{unit}</span>
                {state.unit_intent[unit] && (
                  <span className="text-muted-foreground">- {state.unit_intent[unit]}</span>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* 正在跑的 Agent（spinner + 名 + 第几轮 + 在调什么工具） */}
      {running.length > 0 && (
        <div className="mt-2 space-y-0.5">
          {running.map((a) => (
            <div key={a.name} className="font-mono text-xs">
              <span className="supernova-spinner" aria-hidden /> {a.name}{" "}
              <span className="text-muted-foreground">t{a.turn}</span>{" "}
              {a.last_action_detail ?? a.last_action ?? ""}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
