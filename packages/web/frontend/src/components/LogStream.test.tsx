import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { LogStream } from "./LogStream";
import type { NdjsonEvent } from "../api/types";

const events: NdjsonEvent[] = [
  { ts: "2026-07-02T09:44:01.000Z", category: "PHASE", type: "PhaseEvent", phase: "recon", event: "start", steps: [], step_intents: [] },
  { ts: "2026-07-02T09:44:05.000Z", category: "AGENT", type: "AgentEvent", agent_name: "Injection", event: "start", attempt: 1 },
  { ts: "2026-07-02T09:44:10.000Z", category: "ERROR", type: "ErrorEvent", error_type: "X", message: "boom" },
];

// 行选择器：CAT_CLASS 的 `.ev-*`/`.trace` 是事件色不变量，作为行的稳定 hook
const ROW_SELECTOR = ".ev-phase, .ev-agent, .ev-tool, .ev-llm, .ev-error, .ev-info, .ev-warn, .trace";

describe("LogStream", () => {
  it("容器有 aria-live=polite", () => {
    render(<LogStream events={[]} />);
    // 容器是 aria-live 区域（用 getByRole("log") 或 aria-live 查询）
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

  it("每行含时间戳 + type + 摘要", () => {
    render(<LogStream events={events} />);
    expect(screen.getByText(/09:44:01/)).toBeInTheDocument();
    expect(screen.getAllByText(/PhaseEvent|AgentEvent|ErrorEvent/).length).toBe(3);
  });

  it("events > 500 切 react-window 虚拟滚动（结构断言：行仍按 category 上色）", () => {
    // jsdom 无 layout，react-window FixedSizeList 仍会渲染可见窗口内的 row；
    // 给定固定 height=400/行高 20 → ~20 行可见，首行可见且带正确色类。
    const big: NdjsonEvent[] = Array.from({ length: 600 }, (_, i) => ({
      ts: "2026-07-02T09:44:01.000Z", category: i % 2 === 0 ? "PHASE" : "ERROR",
      type: i % 2 === 0 ? "PhaseEvent" : "ErrorEvent",
      phase: "recon", event: "start", steps: [], step_intents: [],
      error_type: "X", message: "boom",
    } as NdjsonEvent));
    const { container } = render(<LogStream events={big} />);
    // 虚拟列表挂载（FixedSizeList 渲染窗口内的 row，仍带 .ev-* 色类）
    const rows = container.querySelectorAll(ROW_SELECTOR);
    expect(rows.length).toBeGreaterThan(0);
    // 至少一行 ev-phase 或 ev-error（视可见窗口起始位置）
    const colored = Array.from(rows).filter((r) =>
      r.className.includes("ev-phase") || r.className.includes("ev-error"));
    expect(colored.length).toBe(rows.length);
  });
});
