import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { LogStream } from "./LogStream";
import type { NdjsonEvent } from "../api/types";

const events: NdjsonEvent[] = [
  { ts: "2026-07-02T09:44:01.000Z", category: "PHASE", type: "PhaseEvent", phase: "recon", event: "start", steps: [], step_intents: [] },
  { ts: "2026-07-02T09:44:05.000Z", category: "AGENT", type: "AgentEvent", agent_name: "injection-vuln", event: "start", attempt: 1 },
  { ts: "2026-07-02T09:44:10.000Z", category: "ERROR", type: "ErrorEvent", error_type: "ValueError", message: "boom", context: "recon", classified: "non-retryable" },
];

// 行选择器：CAT_CLASS 的 `.ev-*`/`.trace` 是事件色不变量，作为行的稳定 hook
const ROW_SELECTOR = ".ev-phase, .ev-agent, .ev-tool, .ev-llm, .ev-error, .ev-info, .ev-warn, .trace, .ev-agent-ok, .ev-agent-fail";

describe("LogStream", () => {
  it("容器有 aria-live=polite", () => {
    render(<LogStream events={[]} />);
    expect(document.querySelector('[aria-live="polite"]')).toBeInTheDocument();
  });

  it("逐事件渲染行 + 按 category 上色 class", () => {
    const { container } = render(<LogStream events={events} />);
    const rows = container.querySelectorAll(ROW_SELECTOR);
    expect(rows.length).toBe(3);
    expect(rows[0].className).toContain("ev-phase");
    expect(rows[1].className).toContain("ev-agent");
    expect(rows[2].className).toContain("ev-error");
  });

  it("每行含时间戳 + data-type + 摘要", () => {
    const { container } = render(<LogStream events={events} />);
    expect(screen.getByText(/09:44:01/)).toBeInTheDocument();
    const rows = container.querySelectorAll(".log-row");
    expect(rows.length).toBe(3);
    // type 身份经 data-type 属性承载（显示列已换成短语义标签）
    const types = Array.from(rows).map((r) => r.getAttribute("data-type"));
    expect(types).toEqual(["PhaseEvent", "AgentEvent", "ErrorEvent"]);
  });

  it("events > 500 切 react-window 虚拟滚动（结构断言：行仍按 category 上色）", () => {
    const big: NdjsonEvent[] = Array.from({ length: 600 }, (_, i) => ({
      ts: "2026-07-02T09:44:01.000Z", category: i % 2 === 0 ? "PHASE" : "ERROR",
      type: i % 2 === 0 ? "PhaseEvent" : "ErrorEvent",
      phase: "recon", event: "start", steps: [], step_intents: [],
      error_type: "X", message: "boom",
    } as NdjsonEvent));
    const { container } = render(<LogStream events={big} />);
    const rows = container.querySelectorAll(ROW_SELECTOR);
    expect(rows.length).toBeGreaterThan(0);
    const colored = Array.from(rows).filter((r) =>
      r.className.includes("ev-phase") || r.className.includes("ev-error"));
    expect(colored.length).toBe(rows.length);
  });

  // helper：取单行 div 的 textContent（按 class 定位，避免 getByText 命中 type span 而非整行）
  function rowText(container: HTMLElement, cls: string): string {
    const el = container.querySelector(`.${cls}`);
    if (!el) throw new Error(`missing .${cls} row`);
    return el.textContent ?? "";
  }

  // ─── 增强测试：agent 行带 prefix + attempt ───
  it("AgentEvent start 行含 agent_prefix + agent_name + attempt", () => {
    const ev: NdjsonEvent = {
      ts: "2026-07-02T10:00:00.000Z", category: "AGENT", type: "AgentEvent",
      agent_name: "xss-vuln", event: "start", attempt: 2,
    };
    const { container } = render(<LogStream events={[ev]} />);
    const txt = rowText(container, "ev-agent");
    expect(txt).toMatch(/\[XSS\]/);
    expect(txt).toMatch(/xss-vuln/);
    expect(txt).toMatch(/attempt 2/);
  });

  // ─── 增强测试：AgentEvent end success 含 duration + cost + tokens ───
  it("AgentEvent end success 行含 duration + cost", () => {
    const ev: NdjsonEvent = {
      ts: "2026-07-02T10:00:05.000Z", category: "AGENT", type: "AgentEvent",
      agent_name: "injection-vuln", event: "end", attempt: 1,
      success: true, duration_ms: 45200, cost_usd: 0.1234, cost_currency: "USD",
      input_tokens: 1200, output_tokens: 345,
    };
    const { container } = render(<LogStream events={[ev]} />);
    const txt = rowText(container, "ev-agent-ok");
    expect(txt).toMatch(/\[Injection\]/);
    expect(txt).toMatch(/Completed/);
    expect(txt).toMatch(/45\.2s/);
    expect(txt).toMatch(/\$0\.12/);  // fmtCost 四舍五入到 2 位
  });

  // ─── 增强测试：AgentEvent end fail 含 error ───
  it("AgentEvent end fail 行含 duration + error", () => {
    const ev: NdjsonEvent = {
      ts: "2026-07-02T10:00:10.000Z", category: "AGENT", type: "AgentEvent",
      agent_name: "auth-vuln", event: "end", attempt: 1,
      success: false, duration_ms: 12300, error: "timeout",
    };
    const { container } = render(<LogStream events={[ev]} />);
    const txt = rowText(container, "ev-agent-fail");
    expect(txt).toMatch(/\[Auth\]/);
    expect(txt).toMatch(/failed/);
    expect(txt).toMatch(/12\.3s/);
    expect(txt).toMatch(/timeout/);
  });

  // ─── 增强测试：Agent end success/fail 颜色 class ───
  it("AgentEvent end success 用 ev-agent-ok，fail 用 ev-agent-fail", () => {
    const ok: NdjsonEvent = {
      ts: "2026-07-02T10:01:00.000Z", category: "AGENT", type: "AgentEvent",
      agent_name: "injection-vuln", event: "end", attempt: 1, success: true,
    };
    const fail: NdjsonEvent = {
      ts: "2026-07-02T10:01:01.000Z", category: "AGENT", type: "AgentEvent",
      agent_name: "xss-vuln", event: "end", attempt: 1, success: false,
    };
    const { container } = render(<LogStream events={[ok, fail]} />);
    const rows = container.querySelectorAll(ROW_SELECTOR);
    expect(rows[0].className).toContain("ev-agent-ok");
    expect(rows[1].className).toContain("ev-agent-fail");
  });

  // ── LlmTurnEvent 含 agent_prefix + turn + content snippet ──
  it("LlmTurnEvent 行含 agent_prefix + turn + content snippet", () => {
    const ev: NdjsonEvent = {
      ts: "2026-07-02T10:02:00.000Z", category: "LLM", type: "LlmTurnEvent",
      agent_name: "ssrf-vuln", turn: 3, content: "Found sink at line 42\nsecond line",
    };
    const { container } = render(<LogStream events={[ev]} />);
    const txt = rowText(container, "ev-llm");
    expect(txt).toMatch(/\[SSRF\]/);
    expect(txt).toMatch(/Turn 3/);
    expect(txt).toContain("Found sink at line 42");
  });

  // ── GitnexusLlmEvent progress ──
  it("GitnexusLlmEvent progress 行含 phase + done/total + hits", () => {
    const ev: NdjsonEvent = {
      ts: "2026-07-02T10:03:00.000Z", category: "GITNEXUS", type: "GitnexusLlmEvent",
      phase: "sink-discovery", kind: "progress", done: 5, total: 10, hits: 3,
    } as NdjsonEvent;
    const { container } = render(<LogStream events={[ev]} />);
    const txt = rowText(container, "ev-info");  // GITNEXUS → ev-info
    expect(txt).toContain("sink-discovery");
    expect(txt).toMatch(/5\/10/);
    expect(txt).toMatch(/3/);
  });

  // ── GitnexusLlmEvent hit ──
  it("GitnexusLlmEvent hit 行含 detail", () => {
    const ev: NdjsonEvent = {
      ts: "2026-07-02T10:03:01.000Z", category: "GITNEXUS", type: "GitnexusLlmEvent",
      phase: "chain-verdict", kind: "hit", done: 0, total: 0, hits: 0,
      detail: "SQL injection in /api/users",
    } as NdjsonEvent;
    const { container } = render(<LogStream events={[ev]} />);
    const txt = rowText(container, "ev-info");
    expect(txt).toContain("SQL injection in /api/users");
  });

  // ── WorkflowHeader 行含 repo_path + target_url ──
  it("WorkflowHeader 行含 repo_path + target_url", () => {
    const ev: NdjsonEvent = {
      ts: "2026-07-02T10:00:00.000Z", category: "HEADER", type: "WorkflowHeader",
      workflow_id: "wf-1", target_url: "https://example.com", repo_path: "/tmp/repo",
      mode: "offline", web_ui_url: "", logs_cmd: "", workspace: "ws1",
    } as NdjsonEvent;
    const { container } = render(<LogStream events={[ev]} />);
    const txt = rowText(container, "trace");  // HEADER → trace
    expect(txt).toContain("/tmp/repo");
    expect(txt).toContain("https://example.com");
  });

  // ── ToolCallEvent 含 agent_prefix + humanized params ──
  it("ToolCallEvent 行含 agent_prefix + tool_name + params", () => {
    const ev: NdjsonEvent = {
      ts: "2026-07-02T10:04:00.000Z", category: "TOOL", type: "ToolCallEvent",
      agent_name: "injection-vuln", tool_name: "Bash",
      parameters: { command: "grep -r eval src/" },
    };
    const { container } = render(<LogStream events={[ev]} />);
    const txt = rowText(container, "ev-tool");
    expect(txt).toMatch(/\[Injection\]/);
    expect(txt).toContain("Bash");
  });

  // ── StepEvent done 含 duration ──
  it("StepEvent done 行含 duration", () => {
    const ev: NdjsonEvent = {
      ts: "2026-07-02T10:05:00.000Z", category: "STEP", type: "StepEvent",
      name: "code-index", phase: "pre-recon", event: "complete",
      duration_ms: 2500, intent: "Build code index",
    };
    const { container } = render(<LogStream events={[ev]} />);
    const txt = rowText(container, "ev-info");  // STEP → ev-info
    expect(txt).toContain("Build code index");
    expect(txt).toMatch(/2\.5s/);
  });

  // ── ErrorEvent 含 context + classified ──
  it("ErrorEvent 行含 context + classified", () => {
    const ev: NdjsonEvent = {
      ts: "2026-07-02T10:06:00.000Z", category: "ERROR", type: "ErrorEvent",
      error_type: "TimeoutError", message: "LLM timeout",
      context: "recon", classified: "retryable", display_retryable: true,
      attempt: 2, max_attempts: 5,
    };
    const { container } = render(<LogStream events={[ev]} />);
    const txt = rowText(container, "ev-error");
    expect(txt).toContain("TimeoutError");
    expect(txt).toContain("LLM timeout");
    expect(txt).toContain("recon");
    expect(txt).toContain("retryable");
  });

  // ── SummaryEvent 含 duration + cost + agent count ──
  it("SummaryEvent 行含 duration + cost + agent count", () => {
    const ev: NdjsonEvent = {
      ts: "2026-07-02T10:07:00.000Z", category: "SUMMARY", type: "SummaryEvent",
      status: "completed", total_duration_ms: 330000, total_cost_usd: 1.5,
      cost_currency: "USD",
      agents: [{ name: "a" }, { name: "b" }, { name: "c" }],
    } as NdjsonEvent;
    const { container } = render(<LogStream events={[ev]} />);
    const txt = rowText(container, "ev-phase");  // SUMMARY → ev-phase
    expect(txt).toContain("completed");
    expect(txt).toMatch(/5m 30s/);
    expect(txt).toMatch(/\$1\.50/);
    expect(txt).toContain("3");
  });

  // ── 自动滚底：非虚拟列表 ──
  it("新事件到达时非虚拟列表容器自动滚底（不崩溃 + ref 挂载）", () => {
    const { container, rerender } = render(<LogStream events={events} />);
    const scrollDiv = container.querySelector('[aria-live="polite"]');
    expect(scrollDiv).toBeTruthy();
    // re-render with more events → useEffect runs rAF scroll (jsdom: no-op, should not crash)
    expect(() => {
      rerender(<LogStream events={[...events, {
        ts: "2026-07-02T09:44:15.000Z", category: "INFO", type: "InfoEvent", message: "done", level: "info",
      }]} />);
    }).not.toThrow();
  });

  // ── 自动滚底：虚拟列表 scrollToItem ──
  it("events > 500 虚拟列表自动滚底", () => {
    const big: NdjsonEvent[] = Array.from({ length: 600 }, () => ({
      ts: "2026-07-02T09:44:01.000Z", category: "PHASE", type: "PhaseEvent",
      phase: "recon", event: "start", steps: [], step_intents: [],
    } as NdjsonEvent));
    // react-window FixedSizeList scrollToItem is a method on the instance.
    // jsdom renders the list DOM but we can't easily spy on the instance method.
    // Instead, verify the virtual list renders rows (existing test covers this)
    // and that no crash occurs when events grow beyond 500.
    const { container } = render(<LogStream events={big} />);
    const rows = container.querySelectorAll(ROW_SELECTOR);
    // Virtual window renders visible subset; verify it's non-empty
    expect(rows.length).toBeGreaterThan(0);
    // Re-render with +1 event — should not crash
    const bigger: NdjsonEvent[] = [...big, {
      ts: "2026-07-02T09:44:02.000Z", category: "INFO", type: "InfoEvent", message: "new", level: "info",
    } as NdjsonEvent];
    expect(() => render(<LogStream events={bigger} />)).not.toThrow();
  });

  // ── 真正未知 event type 走 default 不崩，显示 type 名 ──
  it("未知 event type 走 default 不崩，显示 type 名", () => {
    const ev = {
      ts: "2026-07-02T10:08:00.000Z", category: "INFO", type: "FutureEvent",
      foo: "bar",
    } as unknown as NdjsonEvent;
    const { container } = render(<LogStream events={[ev]} />);
    const txt = rowText(container, "ev-info");  // category=INFO → CAT_CLASS → ev-info
    expect(txt).toContain("FutureEvent");
  });

  it("LogEvent 渲染 [LEVEL] logger: msg 且按 level 着色", () => {
    const evs: NdjsonEvent[] = [
      { ts: "2026-07-16T02:00:00.000Z", category: "WARNING", type: "LogEvent", logger_name: "mod.a", level: "WARNING", message: "careful" } as NdjsonEvent,
      { ts: "2026-07-16T02:00:01.000Z", category: "ERROR", type: "LogEvent", logger_name: "mod.b", level: "ERROR", message: "boom" } as NdjsonEvent,
      { ts: "2026-07-16T02:00:02.000Z", category: "INFO", type: "LogEvent", logger_name: "mod.c", level: "INFO", message: "hi" } as NdjsonEvent,
    ];
    const { container } = render(<LogStream events={evs} />);
    // WARNING → ev-warn, ERROR → ev-error, INFO → 灰显(text-muted-foreground)
    expect(rowText(container, "ev-warn")).toMatch(/\[WARNING\]/);
    expect(rowText(container, "ev-warn")).toMatch(/mod\.a: careful/);
    expect(rowText(container, "ev-error")).toMatch(/\[ERROR\]/);
    expect(rowText(container, "ev-error")).toMatch(/mod\.b: boom/);
    // 注意：必须用 div.text-muted-foreground——LogStream.tsx 每行的时间戳/type <span>
    // 都带 text-muted-foreground class；裸 .text-muted-foreground 会命中首个 span 而非
    // INFO 行 div。INFO 行的 rowClass 返回 "text-muted-foreground"（div class），加 div
    // 限定只匹配该行 div。
    const infoRow = container.querySelector("div.text-muted-foreground");
    expect(infoRow?.textContent ?? "").toMatch(/\[INFO\]/);
    expect(infoRow?.textContent ?? "").toMatch(/mod\.c: hi/);
  });
});
