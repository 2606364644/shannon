import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DashboardPanel } from "./DashboardPanel";
import type { DashboardState } from "../state/dashboardReducer";

const state: DashboardState = {
  current_phase: "vulnerability-analysis", agents: {}, phase_units: ["Injection", "Xss"],
  unit_status: { Injection: "done", Xss: "running" }, unit_intent: {},
  completed_count: 1, total_cost: 0.5, total_units: 2, completed_units: 1, running_units: ["Xss"],
};
(state as any).agents = {
  Xss: { name: "Xss", status: "running", attempt: 1, turn: 2, last_action: "Bash", last_action_detail: "Bash grep", last_turn_text: "scanning", duration_ms: null, cost_usd: null, error: null },
};

describe("DashboardPanel", () => {
  it("状态条：phase + step N/M + elapsed + cost", () => {
    render(<DashboardPanel state={state} elapsedMs={134000} />);
    expect(screen.getByText(/vulnerability-analysis/)).toBeInTheDocument();
    expect(screen.getByText(/1\/2/)).toBeInTheDocument();       // completed/total units
    expect(screen.getByText(/02:14/)).toBeInTheDocument();       // 134000ms → 02:14
    expect(screen.getByText(/\$0\.50/)).toBeInTheDocument();
  });
  it("运行中 agent 行：spinner + name + turn + last_action", () => {
    const { container } = render(<DashboardPanel state={state} elapsedMs={0} />);
    expect(container.querySelector(".spinner")).toBeInTheDocument();
    expect(screen.getByText(/Xss/)).toBeInTheDocument();
    expect(screen.getByText(/t2/)).toBeInTheDocument();
    expect(screen.getByText(/Bash grep/)).toBeInTheDocument();
  });
});
