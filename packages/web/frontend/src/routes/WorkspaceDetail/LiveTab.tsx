import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useEventSource } from "../../api/useEventSource";
import { scanEventsUrl } from "../../api/client";
import { dashboardReducer, emptyState } from "../../state/dashboardReducer";
import type { DashboardState } from "../../state/dashboardReducer";
import type { ScanEndEvent } from "../../api/types";
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
  const lastApplied = useRef(0);

  // 增量 fold（不动）
  useEffect(() => {
    if (events.length <= lastApplied.current) return;
    setState((s) => events.slice(lastApplied.current).reduce(dashboardReducer, s));
    lastApplied.current = events.length;
  }, [events]);

  // elapsed 从最后一条 PhaseEvent(start) 的 ts 推导（修复漂移 + 进入页面不归零）
  const phaseStartMs = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      if (e.type === "PhaseEvent" && e.event === "start") return Date.parse(e.ts);
    }
    return null;
  }, [events]);
  useEffect(() => {
    if (phaseStartMs == null || Number.isNaN(phaseStartMs)) { setElapsed(0); return; }
    const tick = () => setElapsed(Date.now() - phaseStartMs);
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [phaseStartMs]);

  // scan_end 真实信号是 events 出现 scan_end 事件（status==="closed" 既能是 scan_end 也能是初始未连接，不可靠）
  const scanEnd = useMemo<ScanEndEvent | null>(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].type === "scan_end") return events[i] as ScanEndEvent;
    }
    return null;
  }, [events]);
  const endedCompleted = scanEnd?.status === "completed";
  const endedFailed = scanEnd !== null && !endedCompleted;
  // 重连态 + 空 events + 无 scan_end：扫描可能已中断或暂无进度，纯空面板不友好
  const stalled = status === "error" && events.length === 0 && scanEnd === null;
  const sm = STATUS_MAP[status] ?? STATUS_MAP.closed;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <Badge variant="outline" className={`gap-1 ${sm.cls}`}>
          <span aria-hidden>●</span>{t(sm.labelKey)}
        </Badge>
      </div>
      <DashboardPanel state={state} elapsedMs={elapsed} />
      <LogStream events={events} />
      {stalled && (
        <div role="status" className="rounded-md border border-border bg-card p-3 text-sm text-muted-foreground">
          {t("workspaceDetail.live.stalledHint")}
        </div>
      )}
      {endedCompleted && (
        <div role="status" className="flex items-center gap-3 rounded-md border border-border bg-card p-3 text-sm">
          <span className="text-cyan">{t("workspaceDetail.live.endedTitle")}</span>
          <span className="text-muted-foreground">{t("workspaceDetail.live.endedHint")}</span>
          <Button size="sm" variant="outline" onClick={() => navigate(`/p/${workspace}/scans/${scanId}/report`)}>
            {t("workspaceDetail.live.viewReport")}
          </Button>
        </div>
      )}
      {endedFailed && (
        <div role="status" className="space-y-2 rounded-md border border-yellow/40 bg-card p-3 text-sm">
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
