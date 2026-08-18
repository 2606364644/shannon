import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import i18n from "@/i18n";
import { ScanProgressOverview } from "./ScanProgressOverview";

// === mergedScanEventsUrl 纯函数（全量归并流：认证/白盒/黑盒 run-K 单一 URL）===
describe("mergedScanEventsUrl", () => {
  const base = "/api/workspaces/ws1/scans/scan1/events";
  it("无 rev → 裸归并流 URL", async () => {
    const { mergedScanEventsUrl } = await import("@/api/client");
    expect(mergedScanEventsUrl("ws1", "scan1")).toBe(base);
  });
  it("有 rev → ?rev= 后缀（run 数变化强制重开流）", async () => {
    const { mergedScanEventsUrl } = await import("@/api/client");
    expect(mergedScanEventsUrl("ws1", "scan1", 2)).toBe(`${base}?rev=2`);
  });
  it("isBlackboxSegmentActive 仅作阶段徽章判定（不再决定流 URL）", async () => {
    const { isBlackboxSegmentActive } = await import("@/api/client");
    expect(isBlackboxSegmentActive({ combined: true, bbPhase: "running", selectedRun: "run-1" })).toBe(true);
    expect(isBlackboxSegmentActive({ combined: true, bbPhase: "precheck", selectedRun: "run-1" })).toBe(false);
    expect(isBlackboxSegmentActive({ combined: false, bbPhase: "running", selectedRun: "run-1" })).toBe(false);
  });
});

// === ScanProgressOverview 组件 ===
const eventsState: { events: any[]; status: string } = { events: [], status: "open" };
vi.mock("@/api/useEventSource", () => ({ useEventSource: () => eventsState }));

const TS = "2026-08-14T00:00:00.000Z";
function phaseStart(phase: string, steps: string[] = [], intents: string[] = []) {
  return { ts: TS, category: "PHASE", type: "PhaseEvent", phase, event: "start", steps, step_intents: intents };
}
function stepComplete(name: string, phase: string, error?: string) {
  return { ts: TS, category: "STEP", type: "StepEvent", name, phase, event: "complete", error };
}
function agentStart(name: string) {
  return { ts: TS, category: "AGENT", type: "AgentEvent", agent_name: name, event: "start", attempt: 1 };
}
function toolCall(agent: string, tool: string, parameters: Record<string, unknown> = {}) {
  return { ts: TS, category: "TOOL", type: "ToolCallEvent", agent_name: agent, tool_name: tool, parameters };
}

beforeEach(() => { i18n.changeLanguage("zh"); eventsState.events = []; eventsState.status = "open"; });

describe("ScanProgressOverview", () => {
  it("渲染当前阶段（PhaseEvent fold → current_phase）", () => {
    eventsState.events = [phaseStart("recon")];
    render(<ScanProgressOverview ws="ws" scanId="s1" />);
    expect(screen.getByText("recon")).toBeInTheDocument();
  });

  it("进度条分段渲染 phase_units（含状态），步级明细在 Popover 内", () => {
    eventsState.events = [
      phaseStart("recon", ["pre-recon", "route-map"], ["侦察", "路由图"]),
      stepComplete("pre-recon", "recon"),
    ];
    render(<ScanProgressOverview ws="ws" scanId="s1" />);
    // 常驻态：分段进度条（不展开列表）
    const strip = screen.getByTestId("progress-strip");
    const segs = strip.querySelectorAll("[data-unit]");
    expect(segs).toHaveLength(2);
    expect(strip.querySelector('[data-unit="pre-recon"]')).toHaveAttribute("data-status", "done");
    expect(strip.querySelector('[data-unit="route-map"]')).toHaveAttribute("data-status", "pending");
    expect(screen.queryByText("route-map")).not.toBeInTheDocument();
    // 点 chevron 打开浮层 → 完整步级列表（名 + intent）
    fireEvent.click(screen.getByTestId("progress-details-trigger"));
    expect(screen.getByText("route-map")).toBeInTheDocument();
    expect(screen.getByText("- 路由图")).toBeInTheDocument();
  });

  it("渲染正在跑的 Agent 芯片，详情在 Popover 内", () => {
    eventsState.events = [
      phaseStart("recon", ["pre-recon"]),
      agentStart("recon-agent"),
      toolCall("recon-agent", "Task", { description: "auth analysis" }),
    ];
    render(<ScanProgressOverview ws="ws" scanId="s1" />);
    // 常驻态：芯片显示 agent 名（spinner + t{turn}）
    expect(screen.getByText(/recon-agent/)).toBeInTheDocument();
    // 浮层内展示工具调用详情
    fireEvent.click(screen.getByTestId("progress-details-trigger"));
    expect(screen.getByTestId("progress-details")).toHaveTextContent("auth analysis");
  });

  it("失败步骤 → ✗N 红色计数 + 分段标红", () => {
    eventsState.events = [
      phaseStart("recon", ["a", "b"]),
      stepComplete("a", "recon", "boom"),
    ];
    render(<ScanProgressOverview ws="ws" scanId="s1" />);
    expect(screen.getByTestId("progress-failed-count")).toHaveTextContent("✗1");
    expect(screen.getByTestId("progress-strip").querySelector('[data-unit="a"]'))
      .toHaveAttribute("data-status", "failed");
  });

  it("连接态 open → 已连接徽章", () => {
    eventsState.status = "open";
    render(<ScanProgressOverview ws="ws" scanId="s1" />);
    expect(screen.getByText("已连接")).toBeInTheDocument();
  });

  it("连接态 error → 重连中徽章", () => {
    eventsState.status = "error";
    render(<ScanProgressOverview ws="ws" scanId="s1" />);
    expect(screen.getByText("重连中")).toBeInTheDocument();
  });

  it("无 events 时不崩（current_phase 占位）", () => {
    eventsState.events = [];
    render(<ScanProgressOverview ws="ws" scanId="s1" />);
    // 组件根始终挂载
    expect(screen.getByTestId("scan-progress-overview")).toBeInTheDocument();
  });
});
