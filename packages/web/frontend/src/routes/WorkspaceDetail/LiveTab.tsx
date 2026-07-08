import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useEventSource } from "../../api/useEventSource";
import { dashboardReducer, emptyState } from "../../state/dashboardReducer";
import type { DashboardState } from "../../state/dashboardReducer";
import type { ScanEndEvent } from "../../api/types";
import { DashboardPanel } from "../../components/DashboardPanel";
import { LogStream } from "../../components/LogStream";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

const STATUS_MAP: Record<string, { label: string; cls: string }> = {
  open: { label: "已连接", cls: "border-cyan/40 text-cyan" },
  error: { label: "重连中", cls: "border-yellow/40 text-yellow" },
  closed: { label: "已结束", cls: "border-muted-foreground/40 text-muted-foreground" },
};

// scan_end 非完成态的中文标签（失败/中断/被杀/崩溃）
const END_LABEL: Record<string, string> = {
  failed: "扫描失败",
  interrupted: "扫描已中断",
  killed: "扫描被终止",
  crashed: "扫描崩溃",
};

export default function LiveTab() {
  const { workspace } = useParams<{ workspace: string }>();
  const navigate = useNavigate();
  const { events, status } = useEventSource(`/api/workspaces/${workspace}/events`);
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
          <span aria-hidden>●</span>{sm.label}
        </Badge>
      </div>
      <DashboardPanel state={state} elapsedMs={elapsed} />
      <LogStream events={events} />
      {stalled && (
        <div role="status" className="rounded-md border border-border bg-card p-3 text-sm text-muted-foreground">
          正在重连实时通道，或扫描暂无进度数据。若长时间无更新，扫描可能已因服务重启或进程退出被中断。
        </div>
      )}
      {endedCompleted && (
        <div role="status" className="flex items-center gap-3 rounded-md border border-border bg-card p-3 text-sm">
          <span className="text-cyan">扫描结束</span>
          <span className="text-muted-foreground">可查看完整报告</span>
          <Button size="sm" variant="outline" onClick={() => navigate(`/p/${workspace}/report`)}>
            查看报告
          </Button>
        </div>
      )}
      {endedFailed && (
        <div role="status" className="space-y-2 rounded-md border border-yellow/40 bg-card p-3 text-sm">
          <div className="flex items-center gap-2">
            <span className="text-yellow font-medium">
              {END_LABEL[scanEnd!.status] ?? "扫描未完成"}（{scanEnd!.status}）
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
