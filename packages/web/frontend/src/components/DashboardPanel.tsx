import type { DashboardState } from "../state/dashboardReducer";

function fmtMs(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function DashboardPanel({ state, elapsedMs }: { state: DashboardState; elapsedMs: number }) {
  const running = Object.values(state.agents).filter((a) => a.status === "running");
  return (
    <div className="dashboard-panel">
      <div className="dp-bar mono">
        <span className="ev-phase">{state.current_phase ?? "—"}</span>
        {" · "}
        <span>step {state.completed_units}/{state.total_units}</span>
        {" · "}
        <span>{fmtMs(elapsedMs)}</span>
        {" · "}
        <span>${state.total_cost.toFixed(2)}</span>
      </div>
      <div className="dp-agents">
        {running.map((a) => (
          <div key={a.name} className="dp-agent mono">
            <span className="spinner" /> {a.name} <span className="trace">t{a.turn}</span> {a.last_action_detail ?? a.last_action ?? ""}
          </div>
        ))}
        {running.length === 0 && <div className="trace">无运行中 agent</div>}
      </div>
    </div>
  );
}
