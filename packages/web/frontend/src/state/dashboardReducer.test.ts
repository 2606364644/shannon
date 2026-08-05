import { describe, it, expect } from "vitest";
import { dashboardReducer, emptyState } from "./dashboardReducer";
import { firstNonemptyLine, humanizeToolCall } from "./formatters";
import type { NdjsonEvent } from "../api/types";

// 测试辅助：构造 NdjsonEvent，填默认公共字段。
function ev(e: Partial<NdjsonEvent> & { type: NdjsonEvent["type"] }): NdjsonEvent {
  return { ts: "2026-07-02T09:44:01.123Z", category: "PHASE", ...e } as NdjsonEvent;
}

describe("dashboardReducer — 对齐 core DashboardState.apply", () => {
  it("initial state is empty", () => {
    const s = emptyState();
    expect(s.current_phase).toBeNull();
    expect(s.agents).toEqual({});
    expect(s.completed_count).toBe(0);
    expect(s.total_cost).toBe(0);
  });

  it("PhaseEvent start: 设 current_phase + 重置 phase_units/unit_status", () => {
    const s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", category: "PHASE", phase: "recon", event: "start",
      steps: ["s1", "s2"], step_intents: ["", ""],
    }));
    expect(s.current_phase).toBe("recon");
    expect(s.phase_units).toEqual(["s1", "s2"]);
    expect(s.unit_status).toEqual({});
    expect(s.total_units).toBe(2);
    expect(s.completed_units).toBe(0);
  });

  it("PhaseEvent start 缺 steps/step_intents 不崩（守 state 不冻结成 0/0）", () => {
    // 完全缺 steps + step_intents
    const s1 = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", category: "PHASE", phase: "recon", event: "start",
    }));
    expect(s1.current_phase).toBe("recon");
    expect(s1.phase_units).toEqual([]);
    expect(s1.total_units).toBe(0);
    // 有 steps 但缺 step_intents
    const s2 = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", category: "PHASE", phase: "recon", event: "start", steps: ["a", "b"],
    }));
    expect(s2.phase_units).toEqual(["a", "b"]);
    expect(s2.unit_intent).toEqual({});
  });

  it("PhaseEvent complete: 保留 units", () => {
    const s1 = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", category: "PHASE", phase: "recon", event: "start",
      steps: ["s1"], step_intents: [""],
    }));
    const s2 = dashboardReducer(s1, ev({
      type: "PhaseEvent", category: "PHASE", phase: "recon", event: "complete",
      steps: ["s1"], step_intents: [""],
    }));
    expect(s2.phase_units).toEqual(["s1"]); // complete 不清空
    expect(s2.current_phase).toBe("recon");
  });

  it("StepEvent start/complete: 更新 unit_status（_set_unit 仅声明的 unit）", () => {
    let s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", phase: "recon", event: "start", steps: ["s1"], step_intents: [""],
    }));
    s = dashboardReducer(s, ev({ type: "StepEvent", category: "STEP", name: "s1", phase: "recon", event: "start" }));
    expect(s.unit_status["s1"]).toBe("running");
    s = dashboardReducer(s, ev({ type: "StepEvent", name: "s1", phase: "recon", event: "complete" }));
    expect(s.unit_status["s1"]).toBe("done");
    // 未声明的 unit 被忽略
    s = dashboardReducer(s, ev({ type: "StepEvent", name: "ghost", phase: "recon", event: "start" }));
    expect(s.unit_status["ghost"]).toBeUndefined();
  });

  it("StepEvent complete with error: unit_status=failed", () => {
    let s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", phase: "p", event: "start", steps: ["u"], step_intents: [""],
    }));
    s = dashboardReducer(s, ev({ type: "StepEvent", name: "u", phase: "p", event: "complete", error: "boom" }));
    expect(s.unit_status["u"]).toBe("failed");
    expect(s.completed_units).toBe(1); // terminal
  });

  it("AgentEvent start/end: agents[name] 状态流转 + _set_unit", () => {
    let s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", phase: "vulnerability-analysis", event: "start",
      steps: ["Injection"], step_intents: [""],
    }));
    s = dashboardReducer(s, ev({
      type: "AgentEvent", category: "AGENT", agent_name: "Injection", event: "start", attempt: 1,
    }));
    expect(s.agents["Injection"]?.status).toBe("running");
    expect(s.agents["Injection"]?.attempt).toBe(1);
    expect(s.unit_status["Injection"]).toBe("running");
    s = dashboardReducer(s, ev({
      type: "AgentEvent", agent_name: "Injection", event: "end",
      attempt: 1, duration_ms: 42000, cost_usd: 0.5, success: true,
    }));
    expect(s.agents["Injection"]?.status).toBe("done");
    expect(s.agents["Injection"]?.cost_usd).toBe(0.5);
    expect(s.agents["Injection"]?.duration_ms).toBe(42000);
    expect(s.unit_status["Injection"]).toBe("done");
    expect(s.completed_count).toBe(1);
    expect(s.total_cost).toBe(0.5);
  });

  it("AgentEvent end failed: status=failed (terminal still counts)", () => {
    let s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", phase: "p", event: "start", steps: ["A"], step_intents: [""],
    }));
    s = dashboardReducer(s, ev({ type: "AgentEvent", agent_name: "A", event: "start", attempt: 1 }));
    s = dashboardReducer(s, ev({
      type: "AgentEvent", agent_name: "A", event: "end", attempt: 1, success: false, error: "boom",
    }));
    expect(s.agents["A"]?.status).toBe("failed");
    expect(s.agents["A"]?.error).toBe("boom");
    expect(s.completed_count).toBe(1);
  });

  it("AgentEvent retry: end→start updates attempt back to running", () => {
    let s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", phase: "p", event: "start", steps: ["A"], step_intents: [""],
    }));
    s = dashboardReducer(s, ev({ type: "AgentEvent", agent_name: "A", event: "start", attempt: 1 }));
    s = dashboardReducer(s, ev({ type: "AgentEvent", agent_name: "A", event: "end", attempt: 1, success: false, error: "boom" }));
    s = dashboardReducer(s, ev({ type: "AgentEvent", agent_name: "A", event: "start", attempt: 2 }));
    expect(s.agents["A"]?.status).toBe("running");
    expect(s.agents["A"]?.attempt).toBe(2);
    expect(s.agents["A"]?.error).toBeNull(); // start clears error
  });

  it("AgentEvent end without cost/duration: retains prior values", () => {
    let s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", phase: "p", event: "start", steps: ["A"], step_intents: [""],
    }));
    s = dashboardReducer(s, ev({ type: "AgentEvent", agent_name: "A", event: "start", attempt: 1 }));
    s = dashboardReducer(s, ev({
      type: "AgentEvent", agent_name: "A", event: "end", attempt: 1, duration_ms: 100, cost_usd: 2.0, success: true,
    }));
    s = dashboardReducer(s, ev({
      type: "AgentEvent", agent_name: "A", event: "end", attempt: 1, success: true,
    }));
    expect(s.agents["A"]?.duration_ms).toBe(100);
    expect(s.agents["A"]?.cost_usd).toBe(2.0);
  });

  it("ToolCallEvent: 更新 last_action + last_action_detail (humanizeToolCall)", () => {
    let s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", phase: "p", event: "start", steps: ["A"], step_intents: [""],
    }));
    s = dashboardReducer(s, ev({ type: "AgentEvent", agent_name: "A", event: "start", attempt: 1 }));
    s = dashboardReducer(s, ev({
      type: "ToolCallEvent", category: "TOOL", agent_name: "A", tool_name: "Bash", parameters: { command: "rg -n eval" },
    }));
    expect(s.agents["A"]?.last_action).toBe("Bash");
    expect(s.agents["A"]?.last_action_detail).toBe("command=rg -n eval");
  });

  it("ToolCallEvent on unknown agent: dropped (cur undefined)", () => {
    const s = dashboardReducer(emptyState(), ev({
      type: "ToolCallEvent", agent_name: "Ghost", tool_name: "Bash", parameters: { command: "ls" },
    }));
    expect(s.agents["Ghost"]).toBeUndefined();
  });

  it("LlmTurnEvent: 更新 turn + last_turn_text (firstNonemptyLine)", () => {
    let s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", phase: "p", event: "start", steps: ["A"], step_intents: [""],
    }));
    s = dashboardReducer(s, ev({ type: "AgentEvent", agent_name: "A", event: "start", attempt: 1 }));
    s = dashboardReducer(s, ev({
      type: "LlmTurnEvent", category: "LLM", agent_name: "A", turn: 3, content: "\n\nAnalyzing sinks...\n",
    }));
    expect(s.agents["A"]?.turn).toBe(3);
    expect(s.agents["A"]?.last_turn_text).toBe("Analyzing sinks...");
  });

  it("LlmTurnEvent empty content: keeps prior last_turn_text", () => {
    let s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", phase: "p", event: "start", steps: ["A"], step_intents: [""],
    }));
    s = dashboardReducer(s, ev({ type: "AgentEvent", agent_name: "A", event: "start", attempt: 1 }));
    s = dashboardReducer(s, ev({ type: "LlmTurnEvent", agent_name: "A", turn: 1, content: "Keep me" }));
    s = dashboardReducer(s, ev({ type: "LlmTurnEvent", agent_name: "A", turn: 2, content: "   \n\n  " }));
    expect(s.agents["A"]?.turn).toBe(2);
    expect(s.agents["A"]?.last_turn_text).toBe("Keep me");
  });

  it("ResumeEvent: completed_agents 标 done", () => {
    const s = dashboardReducer(emptyState(), ev({
      type: "ResumeEvent", category: "RESUME", previous_workflow_id: "x", new_workflow_id: "y",
      checkpoint_hash: "h", completed_agents: ["Injection", "Xss"],
    }));
    expect(s.agents["Injection"]?.status).toBe("done");
    expect(s.agents["Xss"]?.status).toBe("done");
    expect(s.completed_count).toBe(2);
  });

  it("ErrorEvent/SummaryEvent/WorkflowHeader: 无状态变化", () => {
    const s0 = emptyState();
    const s1 = dashboardReducer(s0, ev({ type: "ErrorEvent", category: "ERROR", error_type: "X", message: "m" }));
    const s2 = dashboardReducer(s1, ev({ type: "SummaryEvent", category: "SUMMARY", status: "completed" }));
    const s3 = dashboardReducer(s2, ev({
      type: "WorkflowHeader", category: "HEADER", workflow_id: "w", target_url: "u",
      repo_path: "/", mode: "whitebox", web_ui_url: "", logs_cmd: "", workspace: "ws",
    }));
    expect(s3).toEqual(s0);
  });

  it("apply is immutable: original state unchanged", () => {
    const s0 = emptyState();
    const s1 = dashboardReducer(s0, ev({
      type: "PhaseEvent", phase: "recon", event: "start", steps: [], step_intents: [],
    }));
    expect(s0.current_phase).toBeNull();
    expect(s1.current_phase).toBe("recon");
  });

  it("派生: completed_count / total_cost / total_units / completed_units / running_units", () => {
    let s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", phase: "p", event: "start", steps: ["A", "B"], step_intents: ["", ""],
    }));
    s = dashboardReducer(s, ev({ type: "AgentEvent", agent_name: "A", event: "start", attempt: 1 }));
    s = dashboardReducer(s, ev({
      type: "AgentEvent", agent_name: "A", event: "end", attempt: 1, success: true, cost_usd: 1.5,
    }));
    s = dashboardReducer(s, ev({ type: "AgentEvent", agent_name: "B", event: "start", attempt: 1 }));
    expect(s.completed_count).toBe(1); // A done
    expect(s.total_cost).toBe(1.5);
    expect(s.total_units).toBe(2);
    expect(s.completed_units).toBe(1); // A unit done
    expect(s.running_units).toEqual(["B"]);
  });

  it("PhaseEvent start resets unit_intent across phases", () => {
    let s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", phase: "pre-recon", event: "start",
      steps: ["code-index"], step_intents: ["构建调用图"],
    }));
    s = dashboardReducer(s, ev({
      type: "PhaseEvent", phase: "recon", event: "start",
      steps: ["recon"], step_intents: ["侦察"],
    }));
    expect(s.unit_intent).toEqual({ recon: "侦察" });
  });

  it("StepEvent records intent when provided", () => {
    let s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", phase: "pre-recon", event: "start", steps: ["code-index"], step_intents: [""],
    }));
    s = dashboardReducer(s, ev({
      type: "StepEvent", name: "code-index", phase: "pre-recon", event: "start", intent: "构建调用图",
    }));
    expect(s.unit_intent["code-index"]).toBe("构建调用图");
  });

  it("running_units lists in-flight agents and steps", () => {
    let s = dashboardReducer(emptyState(), ev({
      type: "PhaseEvent", phase: "pre-recon", event: "start",
      steps: ["code-index", "pre-recon"], step_intents: ["", ""],
    }));
    s = dashboardReducer(s, ev({ type: "StepEvent", name: "code-index", phase: "pre-recon", event: "start" }));
    s = dashboardReducer(s, ev({ type: "AgentEvent", agent_name: "pre-recon", event: "start", attempt: 1 }));
    expect(s.running_units.sort()).toEqual(["code-index", "pre-recon"]);
    expect(s.completed_units).toBe(0);
  });
});

describe("formatters — 对齐 core formatters.py", () => {
  it("firstNonemptyLine: first non-blank stripped line", () => {
    expect(firstNonemptyLine("\n\nAnalyzing sinks...\n")).toBe("Analyzing sinks...");
    expect(firstNonemptyLine("   \n\n  ")).toBe("");
    expect(firstNonemptyLine(null)).toBe("");
    expect(firstNonemptyLine(undefined)).toBe("");
  });

  it("humanizeToolCall: Bash -> command=key", () => {
    expect(humanizeToolCall("Bash", { command: "rg -n eval" })).toBe("command=rg -n eval");
  });

  it("humanizeToolCall: Bash long command truncated", () => {
    const long = "x".repeat(100);
    const out = humanizeToolCall("Bash", { command: long });
    expect(out.startsWith("command=")).toBe(true);
    expect(out.length).toBeLessThan(100);
    expect(out.endsWith("...")).toBe(true);
  });

  it("humanizeToolCall: Read -> file_path", () => {
    expect(humanizeToolCall("Read", { file_path: "/a/b.ts" })).toBe("file_path=/a/b.ts");
  });

  it("humanizeToolCall: Task -> launching description", () => {
    expect(humanizeToolCall("Task", { description: "deep-analysis" })).toBe("🚀 Launching deep-analysis");
    expect(humanizeToolCall("Task", {})).toBe("🚀 Launching analysis agent");
  });

  it("humanizeToolCall: TodoWrite -> summarize completed/in_progress", () => {
    expect(humanizeToolCall("TodoWrite", { todos: [{ status: "completed", content: "done thing" }] })).toBe("✅ done thing");
    expect(humanizeToolCall("TodoWrite", { todos: [{ status: "in_progress", content: "wip" }] })).toBe("🔄 wip");
    expect(humanizeToolCall("TodoWrite", { todos: [] })).toBe("TodoWrite");
  });

  it("humanizeToolCall: agent-browser navigate -> 🌐 Navigating", () => {
    const out = humanizeToolCall("Bash", { command: "agent-browser --session s1 navigate https://example.com/path" });
    expect(out).toBe("🌐 Navigating to example.com");
  });

  it("humanizeToolCall: playwright-cli click -> 🖱️ Clicking", () => {
    const out = humanizeToolCall("Bash", { command: "playwright-cli -s=abc click #submit" });
    expect(out).toBe("🖱️ Clicking #submit");
  });

  it("humanizeToolCall: unknown tool -> first 2 params", () => {
    expect(humanizeToolCall("Custom", { a: 1, b: 2 })).toBe("a=1, b=2");
    expect(humanizeToolCall("Custom", { a: 1, b: 2, c: 3 })).toBe("a=1, b=2, ...");
  });

  it("humanizeToolCall: non-object params -> safe default", () => {
    expect(humanizeToolCall("Custom", null)).toBe("");
    expect(humanizeToolCall("Custom", "string")).toBe("");
  });
});
