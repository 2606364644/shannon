import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import type { DashboardState } from "../state/dashboardReducer";
import { fmtCost } from "../utils/currency";

function fmtMs(ms: number): string {
  if (ms < 0) ms = 0; // 负耗时无意义（跨时钟残差/边界），夹紧到 0
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  const pad2 = (n: number) => String(n).padStart(2, "0");
  if (h > 0) return `${h}h ${pad2(m)}m ${pad2(s)}s`;
  if (m > 0) return `${m}m ${pad2(s)}s`;
  return `${s}s`;
}

const UNIT_STATUS_CLS: Record<string, string> = {
  running: "text-cyan",
  done: "text-green",
  failed: "text-red",
};

function fmtStartedAt(ms: number): string {
  // Unix ms -> 浏览器本地时区可读串（toLocaleString 自动按用户时区）。
  return new Date(ms).toLocaleString();
}

// SSE「最后事件多久前」—— 友好相对时间（刚刚 / 秒 / 分钟 / 小时），避免 "13602 秒前" 这种裸大数。
function fmtLastEventAgo(lastEventMs: number | null | undefined, t: TFunction): string | null {
  if (lastEventMs == null || Number.isNaN(lastEventMs)) return null;
  const sec = Math.max(0, Math.floor((Date.now() - lastEventMs) / 1000));
  if (sec < 3) return t("dashboard.lastEventJustNow");
  if (sec < 60) return t("dashboard.lastEventAgo", { seconds: sec });
  if (sec < 3600) return t("dashboard.lastEventMinutesAgo", { minutes: Math.floor(sec / 60) });
  return t("dashboard.lastEventHoursAgo", { hours: (sec / 3600).toFixed(1) });
}

// 指标格：label（xs muted uppercase）+ value（mono tabular-nums 等宽对齐），垂直堆叠。
function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground/80">{label}</div>
      <div className="mt-0.5 truncate font-mono text-sm tabular-nums text-foreground">{value}</div>
    </div>
  );
}

export interface DashboardPanelProps {
  state: DashboardState;
  elapsedMs: number;
  // ── 开始时间 / 总耗时 / cost 兜底 ──
  startedAtMs?: number | null;
  /** 总扫描耗时（运行中由外部每秒 tick 传入；完成时 = completedAt - startedAt）。 */
  totalElapsedMs?: number | null;
  /** 扫描完成时间（Unix ms），仅完成态有意义。 */
  completedAtMs?: number | null;
  /** 后端 getScan 的累计 cost（完成时兜底，取 max(SSE 累积, 后端)）。 */
  finalCost?: number | null;
  finalCostCurrency?: string | null;
  // ── SSE 实时性指示 ──
  lastEventMs?: number | null;
  eventsCount?: number;
}

export function DashboardPanel({
  state, elapsedMs,
  startedAtMs, totalElapsedMs, completedAtMs,
  finalCost, finalCostCurrency,
  lastEventMs, eventsCount,
}: DashboardPanelProps) {
  const { t } = useTranslation();
  const running = Object.values(state.agents).filter((a) => a.status === "running");

  // cost：完成时取 max(SSE 累积, 后端兜底)；运行中用 SSE 累积。
  const cost = completedAtMs != null && finalCost != null
    ? Math.max(state.total_cost, finalCost)
    : state.total_cost;
  const costCurrency = finalCostCurrency ?? state.cost_currency;

  const lastEventLabel = fmtLastEventAgo(lastEventMs, t);

  // 计数可见性：进度仅 total_units>0 显（否则 0/0 像 bug）；Agent 仅 agents 非空显；
  // 事件计数恒显（最稳的活跃信号）。分隔符按前置项条件渲染，避免悬空 ·。
  const agentTotal = Object.keys(state.agents).length;
  const showProgress = state.total_units > 0;
  const showAgent = agentTotal > 0;
  const hasCounter = showProgress || showAgent;

  return (
    <div className="rounded-md border border-border bg-card p-3 shadow-[var(--shadow-card)]">
      {/* 头条：现在在干什么（phase，coral 主角）+ 是否在动（实时脉冲 + 相对时间） */}
      <div className="flex items-start justify-between gap-3">
        <span className="font-sans text-base font-semibold leading-tight text-primary">
          {state.current_phase ?? "-"}
        </span>
        {lastEventLabel && (
          <span className="flex shrink-0 items-center gap-1.5 whitespace-nowrap font-mono text-xs text-muted-foreground">
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-cyan motion-reduce:animate-none" aria-hidden />
            {lastEventLabel}
          </span>
        )}
      </div>

      {/* 计数副标题：进度 · Agent · 事件（flex-wrap，窄屏可换行） */}
      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono text-xs text-muted-foreground">
        {showProgress && (
          <span>{t("dashboard.progress")} {state.completed_units}/{state.total_units}</span>
        )}
        {showProgress && showAgent && <span aria-hidden className="opacity-40">·</span>}
        {showAgent && (
          <span>Agent {state.completed_count}/{agentTotal}</span>
        )}
        {eventsCount != null && hasCounter && <span aria-hidden className="opacity-40">·</span>}
        {eventsCount != null && (
          <span>{t("dashboard.eventsCount")} {eventsCount}</span>
        )}
      </div>

      {/* 关键指标（hairline 分隔）：阶段耗时 / 总耗时 / 费用 —— label 上 value 下，终结「两个耗时分不清 + cost 被埋」 */}
      <div className="mt-3 grid grid-cols-1 gap-2 border-t border-border pt-3 sm:grid-cols-3 sm:gap-3">
        <Metric label={t("dashboard.phaseDuration")} value={fmtMs(elapsedMs)} />
        <Metric label={t("dashboard.totalDuration")} value={totalElapsedMs != null ? fmtMs(totalElapsedMs) : "—"} />
        <Metric label={t("dashboard.cost")} value={fmtCost(cost, costCurrency)} />
      </div>

      {/* 元数据：开始时间 */}
      {startedAtMs != null && (
        <div className="mt-2 font-mono text-xs text-muted-foreground">
          {t("dashboard.startedAt")} {fmtStartedAt(startedAtMs)}
        </div>
      )}

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
