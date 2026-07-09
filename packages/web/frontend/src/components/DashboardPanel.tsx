import { useTranslation } from "react-i18next";
import type { DashboardState } from "../state/dashboardReducer";
import { fmtCost } from "../utils/currency";

function fmtMs(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

const UNIT_STATUS_CLS: Record<string, string> = {
  running: "text-cyan",
  done: "text-green",
  failed: "text-red",
};

export function DashboardPanel({ state, elapsedMs }: { state: DashboardState; elapsedMs: number }) {
  const { t } = useTranslation();
  const running = Object.values(state.agents).filter((a) => a.status === "running");
  return (
    <div className="rounded-md border border-border bg-card p-3 shadow-[var(--shadow-card)]">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-sm">
        <span className="font-bold text-cyan">{state.current_phase ?? "—"}</span>
        <span className="text-muted-foreground">step {state.completed_units}/{state.total_units}</span>
        <span className="text-muted-foreground">agents {state.completed_count}/{Object.keys(state.agents).length}</span>
        <span className="text-muted-foreground">{fmtMs(elapsedMs)}</span>
        <span className="text-muted-foreground">{fmtCost(state.total_cost, state.cost_currency)}</span>
      </div>
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
                  <span className="text-muted-foreground">— {state.unit_intent[unit]}</span>
                )}
              </div>
            );
          })}
        </div>
      )}
      <div className="mt-2 space-y-0.5">
        {running.map((a) => (
          <div key={a.name} className="font-mono text-xs">
            <span className="shannon-spinner" aria-hidden /> {a.name}{" "}
            <span className="text-muted-foreground">t{a.turn}</span>{" "}
            {a.last_action_detail ?? a.last_action ?? ""}
          </div>
        ))}
        {running.length === 0 && <div className="text-xs text-muted-foreground">{t("dashboard.noRunningAgents")}</div>}
      </div>
    </div>
  );
}
