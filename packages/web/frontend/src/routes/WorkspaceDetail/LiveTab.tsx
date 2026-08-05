import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useEventSource } from "../../api/useEventSource";
import { getScan, scanEventsUrl } from "../../api/client";
import { dashboardReducer, emptyState } from "../../state/dashboardReducer";
import type { DashboardState } from "../../state/dashboardReducer";
import type { ScanEndEvent, SessionData } from "../../api/types";
import { parseEventTs } from "../../utils/eventTs";
import { DashboardPanel } from "../../components/DashboardPanel";
import { LogStream } from "../../components/LogStream";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

const STATUS_MAP: Record<string, { labelKey: string; cls: string }> = {
  open: { labelKey: "workspaceDetail.live.statusOpen", cls: "border-cyan/40 text-cyan" },
  error: { labelKey: "workspaceDetail.live.statusError", cls: "border-yellow/40 text-yellow" },
  closed: { labelKey: "workspaceDetail.live.statusClosed", cls: "border-muted-foreground/40 text-muted-foreground" },
};

// scan_end 非完成态的中文标签 key（失败/中断/被杀/崩溃）
const END_LABEL: Record<string, string> = {
  failed: "workspaceDetail.live.endFailed",
  interrupted: "workspaceDetail.live.endInterrupted",
  killed: "workspaceDetail.live.endKilled",
  crashed: "workspaceDetail.live.endCrashed",
};

export default function LiveTab() {
  const { t } = useTranslation();
  const { workspace, scanId } = useParams<{ workspace: string; scanId: string }>();
  const navigate = useNavigate();
  const { events, status } = useEventSource(
    workspace && scanId ? scanEventsUrl(workspace, scanId) : "",
  );
  const [state, setState] = useState<DashboardState>(emptyState);
  const [elapsed, setElapsed] = useState(0);
  const [totalElapsed, setTotalElapsed] = useState(0);
  const [meta, setMeta] = useState<SessionData | null>(null);
  const lastApplied = useRef(0);

  // 增量 fold（不动）
  useEffect(() => {
    if (events.length <= lastApplied.current) return;
    setState((s) => events.slice(lastApplied.current).reduce(dashboardReducer, s));
    lastApplied.current = events.length;
  }, [events]);

  // getScan 取开始时间 / 总耗时基准 / cost 兜底（仿 OverviewTab，一次性 fetch）。
  useEffect(() => {
    if (!workspace || !scanId) return;
    let cancelled = false;
    getScan(workspace, scanId)
      .then((s) => { if (!cancelled) setMeta(s); })
      .catch(() => { /* 降级：仅靠 SSE 数据，不阻塞 live 页 */ });
    return () => { cancelled = true; };
  }, [workspace, scanId]);

  const startedAtMs = meta?.created_at != null ? meta.created_at * 1000 : null;
  const completedAtMs = meta?.completed_at != null ? meta.completed_at * 1000 : null;

  // scan_end 真实信号是 events 出现 scan_end 事件（status==="closed" 既能是 scan_end 也能是初始未连接，不可靠）
  const scanEnd = useMemo<ScanEndEvent | null>(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].type === "scan_end") return events[i] as ScanEndEvent;
    }
    return null;
  }, [events]);
  const endedCompleted = scanEnd?.status === "completed";
  const endedFailed = scanEnd !== null && !endedCompleted;
  // 已完成：后端 completed_at 或 events scan_end completed 任一为真（双兜底——后端未回 completed_at 时靠 scan_end 停表）。
  const isCompleted = completedAtMs != null || endedCompleted;
  // 完成时刻（ms）：优先后端 completed_at，其次 scan_end 事件 ts。
  const endMs = completedAtMs != null ? completedAtMs : (scanEnd ? parseEventTs(scanEnd.ts) : null);
  const effectiveEndMs = endMs != null && Number.isFinite(endMs) ? endMs : null;

  // elapsed 从最后一条 PhaseEvent(start) 的 ts 推导（修复漂移 + 进入页面不归零）。
  // ts 归一化（parseEventTs）：无时区串当 UTC（worker 容器 UTC），带 Z/+00:00 原样。
  const phaseStartMs = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      if (e.type === "PhaseEvent" && e.event === "start") return parseEventTs(e.ts);
    }
    return null;
  }, [events]);

  // 最后一条事件的 ts（任意类型）-> SSE 实时性「最后事件 X 秒前」。
  const lastEventMs = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const ms = parseEventTs(events[i].ts);
      if (!Number.isNaN(ms)) return ms;
    }
    return null;
  }, [events]);

  useEffect(() => {
    if (phaseStartMs == null || Number.isNaN(phaseStartMs)) { setElapsed(0); return; }
    // 扫描已完成：阶段耗时定格（completedAt/scan_end 时刻 - phaseStart），不再 tick。
    if (isCompleted) {
      setElapsed((effectiveEndMs ?? Date.now()) - phaseStartMs);
      return;
    }
    const tick = () => setElapsed(Date.now() - phaseStartMs);
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [phaseStartMs, isCompleted, effectiveEndMs]);

  // 总耗时：完成 = (completed_at - created_at)；运行中 = now - created_at（每秒 tick）。
  useEffect(() => {
    if (startedAtMs == null) { setTotalElapsed(0); return; }
    // 扫描已完成：总耗时定格（completedAt/scan_end 时刻 - createdAt），不再 tick。
    if (isCompleted) {
      setTotalElapsed((effectiveEndMs ?? Date.now()) - startedAtMs);
      return;
    }
    const tick = () => setTotalElapsed(Date.now() - startedAtMs);
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [startedAtMs, isCompleted, effectiveEndMs]);

  // scan_end completed 时 refetch getScan 取最终 completed_at + total_cost_usd 兜底。
  useEffect(() => {
    if (!endedCompleted || !workspace || !scanId) return;
    getScan(workspace, scanId)
      .then((s) => setMeta(s))
      .catch(() => { /* 兜底失败不阻塞，沿用 SSE 累积 */ });
  }, [endedCompleted, workspace, scanId]);

  // 重连态 + 空 events + 无 scan_end：扫描可能已中断或暂无进度，纯空面板不友好
  const stalled = status === "error" && events.length === 0 && scanEnd === null;
  const sm = STATUS_MAP[status] ?? STATUS_MAP.closed;

  // cost 兜底：完成时取后端 metrics.total_cost_usd（SSE 可能漏累积）。
  const finalCost = isCompleted ? (meta?.metrics?.total_cost_usd ?? null) : null;
  const finalCostCurrency = meta?.metrics?.cost_currency ?? null;

  // 自适应控制台布局：根 max-h（100dvh-14rem，与 LogsTab 同口径=TopBar+main py+scan header+tabs）封顶，
  // 不再强制满高 -> 内容少时不溢出视口、无外层滚动条；指标卡/提示框 shrink-0 钉住，LogStream fill 撑满
  // 中间剩余空间，事件多时 flex-1 min-h-0 压缩独立 tail 内滚。max-h 对 calc 误差高容忍（偏大仅上限松）。
  return (
    <div className="flex max-h-[calc(100dvh-14rem)] min-h-[320px] flex-col gap-3">
      <div className="flex shrink-0 items-center justify-between">
        <Badge variant="outline" className={`gap-1 ${sm.cls}`}>
          <span aria-hidden>●</span>{t(sm.labelKey)}
        </Badge>
      </div>
      <div className="shrink-0">
        <DashboardPanel
          state={state}
          elapsedMs={elapsed}
          startedAtMs={startedAtMs}
          totalElapsedMs={totalElapsed}
          completedAtMs={completedAtMs}
          finalCost={finalCost}
          finalCostCurrency={finalCostCurrency}
          lastEventMs={lastEventMs}
          eventsCount={events.length}
        />
      </div>
      <LogStream events={events} fill />
      {stalled && (
        <div role="status" className="shrink-0 rounded-md border border-border bg-card p-3 text-sm text-muted-foreground">
          {t("workspaceDetail.live.stalledHint")}
        </div>
      )}
      {endedCompleted && (
        <div role="status" className="flex shrink-0 items-center gap-3 rounded-md border border-border bg-card p-3 text-sm">
          <span className="text-cyan">{t("workspaceDetail.live.endedTitle")}</span>
          <span className="text-muted-foreground">{t("workspaceDetail.live.endedHint")}</span>
          <Button size="sm" variant="outline" onClick={() => navigate(`/p/${workspace}/scans/${scanId}/report`)}>
            {t("workspaceDetail.live.viewReport")}
          </Button>
        </div>
      )}
      {endedFailed && (
        <div role="status" className="shrink-0 space-y-2 rounded-md border border-yellow/40 bg-card p-3 text-sm">
          <div className="flex items-center gap-2">
            <span className="text-yellow font-medium">
              {t(END_LABEL[scanEnd!.status] ?? "workspaceDetail.live.endIncomplete")}（{scanEnd!.status}）
            </span>
          </div>
          {scanEnd!.stderr_tail && (
            <pre className="whitespace-pre-wrap break-all rounded bg-muted/40 p-2 text-xs text-muted-foreground">
              {scanEnd!.stderr_tail}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
