import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useEventSource } from "../../api/useEventSource";
import { dashboardReducer, emptyState } from "../../state/dashboardReducer";
import type { DashboardState } from "../../state/dashboardReducer";
import { DashboardPanel } from "../../components/DashboardPanel";
import { LogStream } from "../../components/LogStream";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

const STATUS_MAP: Record<string, { label: string; cls: string }> = {
  open: { label: "已连接", cls: "border-cyan/40 text-cyan" },
  error: { label: "重连中", cls: "border-yellow/40 text-yellow" },
  closed: { label: "已结束", cls: "border-muted-foreground/40 text-muted-foreground" },
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
  const scanEnded = useMemo(() => events.some((e) => e.type === "scan_end"), [events]);
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
      {scanEnded && (
        <div role="status" className="flex items-center gap-3 rounded-md border border-border bg-card p-3 text-sm">
          <span className="text-cyan">扫描结束</span>
          <span className="text-muted-foreground">可查看完整报告</span>
          <Button size="sm" variant="outline" onClick={() => navigate(`/p/${workspace}/report`)}>
            查看报告
          </Button>
        </div>
      )}
    </div>
  );
}
