import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import type { SessionData, SessionMetrics } from "../../api/types";
import { apiGet } from "../../api/client";
import { StatusBadge } from "../../components/StatusBadge";

function fmtMs(ms: number): string {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function OverviewTab() {
  const { workspace } = useParams<{ workspace: string }>();
  const [s, setS] = useState<SessionData | null>(null);
  useEffect(() => { apiGet<SessionData>(`/workspaces/${workspace}`).then(setS); }, [workspace]);
  if (!s?.metrics) return <div className="trace">无 metrics</div>;
  const m = s.metrics;
  const statusConflict = !!(s.status && s.session?.status && s.status !== s.session.status);

  return (
    <div className="overview">
      <div className="ov-statusbar">
        <StatusBadge status={s.status ?? s.session?.status ?? "?"} /> {s.scan_type} {s.repo_path}
        {statusConflict && <span className="ev-warn"> ⚠ 顶层 {s.status} vs session.{s.session!.status}（归一未覆盖顶层，已 flag 后端）</span>}
      </div>
      <div className="big-numbers mono">
        <div><span className="big">${m.total_cost_usd.toFixed(2)}</span> <span className="trace">total cost</span></div>
        <div><span className="big">{fmtMs(m.total_duration_ms)}</span> <span className="trace">duration</span></div>
        <div><span className="big">{Object.keys(m.agents).length}</span> <span className="trace">agents</span></div>
      </div>
      <PhaseWaterfall phases={m.phases} fmt={fmtMs} />
      <AgentTable agents={m.agents} fmt={fmtMs} />
    </div>
  );
}

function PhaseWaterfall({ phases, fmt }: { phases: SessionMetrics["phases"]; fmt: (ms: number) => string }) {
  const entries = Object.entries(phases);
  return (
    <div className="phase-waterfall">
      <h3>阶段瀑布</h3>
      <div className="pw-bars">
        {entries.map(([name, p]) => (
          <div key={name} className="pw-bar" style={{ width: `${p.duration_percentage}%` }} title={`${name}: ${p.duration_percentage}%`}>
            <div className="pw-name">{name}</div>
            <div className="pw-meta mono">{p.duration_percentage}% · {fmt(p.duration_ms)} · ${p.cost_usd.toFixed(2)} · {p.agent_count}a</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function AgentTable({ agents, fmt }: { agents: SessionMetrics["agents"]; fmt: (ms: number) => string }) {
  return (
    <table className="ledger mono agent-table">
      <thead><tr><th>agent</th><th>duration</th><th>cost</th><th>attempt</th><th>model</th></tr></thead>
      <tbody>
        {Object.entries(agents).map(([name, a]) => {
          const warned = a.attempt_number > 1 || !!a.error;
          const cls = a.success === false ? "ev-agent-fail" : warned ? "ev-warn" : "";
          return (
            <tr key={name} className={cls}>
              <td>{name}</td><td>{fmt(a.duration_ms)}</td><td>${a.cost_usd.toFixed(2)}</td>
              <td>{warned ? `⚠ ${a.attempt_number}${a.error ? `(${a.error.slice(0, 20)})` : ""}` : a.attempt_number}</td>
              <td className="trace">{a.model}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
