import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { useEventSource } from "../../api/useEventSource";
import { dashboardReducer, emptyState, type DashboardState } from "../../state/dashboardReducer";
import { DashboardPanel } from "../../components/DashboardPanel";
import { LogStream } from "../../components/LogStream";

export function LiveTab() {
  const { workspace } = useParams<{ workspace: string }>();
  const { events, status } = useEventSource(`/api/workspaces/${workspace}/events`);
  const [state, setState] = useState<DashboardState>(emptyState());
  const [elapsed, setElapsed] = useState(0);
  const lastApplied = useRef(0);

  // 增量 reduce：只对新增事件 reduce（非全量重放，性能）
  useEffect(() => {
    if (events.length <= lastApplied.current) return;
    setState((s) => events.slice(lastApplied.current).reduce(dashboardReducer, s));
    lastApplied.current = events.length;
  }, [events]);

  // 本地 elapsed 自增（零后端 tick）
  useEffect(() => {
    const start = Date.now();
    const t = setInterval(() => setElapsed(Date.now() - start), 1000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="live-tab">
      <DashboardPanel state={state} elapsedMs={elapsed} />
      <LogStream events={events} />
      {status === "closed" && <div className="ev-info">扫描结束 —— 可切到「报告」tab 查看结果</div>}
    </div>
  );
}
