import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DashboardPanel } from "./DashboardPanel";
import { emptyState } from "../state/dashboardReducer";
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
    // Xss 现在既作为 phase_units 单元名出现，也作为运行中 agent 名出现
    expect(screen.getAllByText(/Xss/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/t2/)).toBeInTheDocument();
    expect(screen.getByText(/Bash grep/)).toBeInTheDocument();
  });

  it("渲染 step 进度与 intent（unit_status 三态 + unit_intent 文案）", () => {
    const intentState = {
      ...emptyState(),
      current_phase: "vuln",
      phase_units: ["injection"],
      unit_status: { injection: "running" },
      unit_intent: { injection: "SQLi 候选识别" },
      completed_units: 0,
      total_units: 1,
      running_units: ["injection"],
    } as DashboardState;
    render(<DashboardPanel state={intentState} elapsedMs={0} />);
    // intent 文案渲染（unique 文本，可直接 getByText）
    expect(screen.getByText(/SQLi 候选识别/)).toBeInTheDocument();
    // unit 名称渲染
    expect(screen.getByText(/^injection$/)).toBeInTheDocument();
  });

  it("agents 完成计数渲染 completed_count（顶栏 agents N/M）", () => {
    // 选不包含 "3" 的伴随字段：total_cost=0（不是 0.30），step 0/1（不含 3），elapsed 0
    const completedState = {
      ...emptyState(),
      current_phase: "recon",
      phase_units: ["discover"],
      unit_status: {},
      unit_intent: {},
      completed_count: 3,
      total_cost: 0,
      completed_units: 0,
      total_units: 1,
    } as DashboardState;
    render(<DashboardPanel state={completedState} elapsedMs={0} />);
    // 精确断言：顶栏的 agents 计数 span "agents 3/0"
    // (completed_count=3, agents={} → 0 total；伴随字段无 3 不会假阳性)
    expect(screen.getByText(/agents\s+3\/0/)).toBeInTheDocument();
  });
});
