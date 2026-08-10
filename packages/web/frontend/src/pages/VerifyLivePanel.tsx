// 块4: 认证「测试登录」实时过程面板——步骤条（DashboardPanel phase_units）+ 实时日志（LogStream）。
// CredentialRow 在测试时挂本组件（key=runKey 每 test 重挂载 fresh state）。本次 run 的 SSE 流
// （verifyEventsUrl）→ useEventSource → dashboardReducer 增量 fold → DashboardPanel + LogStream。
// scan_end 出现 → onComplete（CredentialRow 拉终态 verify-status + refresh）。复用 LiveTab 同款管线。
import { useEffect, useRef, useState } from "react";
import { useEventSource } from "@/api/useEventSource";
import { verifyEventsUrl } from "@/api/authProfiles";
import { dashboardReducer, emptyState } from "@/state/dashboardReducer";
import type { DashboardState } from "@/state/dashboardReducer";
import { DashboardPanel } from "@/components/DashboardPanel";
import { LogStream } from "@/components/LogStream";

interface Props {
  ws: string;
  pid: string;
  cid: string;
  workflowId: string;
  probeDir: string;
  /** scan_end 观测到的回调（CredentialRow 拉终态 verify-status + refresh）。仅触发一次。 */
  onComplete: (workflowId: string, probeDir: string) => void;
}

export function VerifyLivePanel({ ws, pid, cid, workflowId, probeDir, onComplete }: Props) {
  const url = verifyEventsUrl(ws, pid, cid, workflowId, probeDir);
  const { events } = useEventSource(url);   // stopType 默认 scan_end
  const [state, setState] = useState<DashboardState>(emptyState);
  const lastApplied = useRef(0);

  // 增量 fold（不动，对齐 LiveTab）
  useEffect(() => {
    if (events.length <= lastApplied.current) return;
    setState((s) => events.slice(lastApplied.current).reduce(dashboardReducer, s));
    lastApplied.current = events.length;
  }, [events]);

  // scan_end → finalize（仅一次）
  const finalized = useRef(false);
  useEffect(() => {
    if (finalized.current) return;
    if (events.some((e) => e.type === "scan_end")) {
      finalized.current = true;
      onComplete(workflowId, probeDir);
    }
  }, [events, workflowId, probeDir, onComplete]);

  return (
    <div className="space-y-2">
      <DashboardPanel state={state} elapsedMs={0} eventsCount={events.length} />
      <LogStream events={events} />
    </div>
  );
}
