import { fireEvent, render, screen } from "@testing-library/react";
import type { CorrelationTopologyAnalysis } from "@/api/types";
import { CorrelationTopologyAnalysisPanel } from "./TopologyAnalysisPanel";

const baseHandlers = {
  onStart: vi.fn(), onRetry: vi.fn(), onCancel: vi.fn(), onManual: vi.fn(),
};

it("shows status, cost, cache and manual fallback", () => {
  const onManual = vi.fn();
  const onRetry = vi.fn();
  render(<CorrelationTopologyAnalysisPanel
    analysis={{
      analysis_id: "topology-1", workspace: "ws1", status: "completed", repos: ["a", "b"],
      cache_hit: true, progress: 100,
      usage: { input_tokens: 1, output_tokens: 2, cost_usd: 3, cost_currency: "CNY" },
    }}
    starting={false} error={null} logLines={[]}
    onStart={vi.fn()} onRetry={onRetry} onCancel={vi.fn()} onManual={onManual}
  />);
  expect(screen.getByText(/completed/i)).toBeInTheDocument();
  expect(screen.getByText(/¥3\.00/)).toBeInTheDocument();
  expect(screen.getByText(/cache/i)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /analyze again|重新分析/i }));
  expect(onRetry).toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /manual/i }));
  expect(onManual).toHaveBeenCalled();
});

it("renders live audit trail while running", () => {
  render(<CorrelationTopologyAnalysisPanel
    analysis={{ analysis_id: "topology-2", workspace: "ws1", status: "running", repos: ["a", "b"], progress: 20 }}
    starting={false} error={null} logDropped={42}
    logLines={[
      { no: 0, ts: "2026-09-03T18:00:00Z", type: "tool_start", tool: "read_file", summary: "{'path': '/repos/gw/main.go'}" },
      { no: 1, ts: "2026-09-03T18:00:01Z", type: "tool_end", summary: "func main() {" },
      { no: 2, ts: "2026-09-03T18:00:02Z", type: "assistant_turn", summary: "turn 3: gateway calls identity" },
      { no: 3, ts: "2026-09-03T18:00:03Z", type: "error", summary: "boom" },
    ]}
    {...baseHandlers}
  />);
  expect(screen.getByText(/main\.go/)).toBeInTheDocument();
  expect(screen.getByText(/gateway calls identity/)).toBeInTheDocument();
  expect(screen.getByText(/boom/)).toBeInTheDocument();
  expect(screen.getByText(/42 ↑/)).toBeInTheDocument();
});

// 行网格对齐扫描 live 页 LogStream 同款 CSS（log-row/log-gutter/log-ts/log-icon/log-tag/
// log-body）+ ev-* 语义类型色：三处日志框（live/跨仓关联/认证测试）统一视觉语言。
it("renders audit lines as log-row grid with ev-* type colors", () => {
  const { container } = render(<CorrelationTopologyAnalysisPanel
    analysis={{ analysis_id: "topology-3", workspace: "ws1", status: "running", repos: ["a"], progress: 10 }}
    starting={false} error={null}
    logLines={[
      { no: 0, ts: "2026-09-03T18:00:00Z", type: "tool_start", tool: "read_file", summary: "main.go" },
      { no: 1, ts: "2026-09-03T18:00:02Z", type: "assistant_turn", summary: "turn 3" },
      { no: 2, ts: "2026-09-03T18:00:03Z", type: "error", summary: "boom" },
    ]}
    {...baseHandlers}
  />);
  const rows = container.querySelectorAll(".log-row");
  expect(rows).toHaveLength(3);
  // 固定列结构：色带|时间|图标|TAG|body
  expect(rows[0].querySelector(".log-gutter")).not.toBeNull();
  expect(rows[0].querySelector(".log-ts")?.textContent).not.toBe("");
  expect(rows[0].querySelector(".log-tag")?.textContent).toBe("TOOL");
  expect(rows[0].querySelector(".log-body")?.textContent).toContain("read_file: main.go");
  // 类型色对齐 live 页语义：tool=ev-tool / llm=ev-llm / error=ev-error
  expect(rows[0].className).toContain("ev-tool");
  expect(rows[1].className).toContain("ev-llm");
  expect(rows[1].querySelector(".log-tag")?.textContent).toBe("LLM");
  expect(rows[2].className).toContain("ev-error");
});

it("hides audit trail when idle with no lines", () => {
  const { container } = render(<CorrelationTopologyAnalysisPanel
    analysis={null} starting={false} error={null} logLines={[]}
    onStart={vi.fn()} onRetry={vi.fn()} onCancel={vi.fn()} onManual={vi.fn()}
  />);
  expect(container.querySelector(".font-mono")).toBeNull();
});

it("renders history entries and forwards selection (restore without re-analysis)", () => {
  const onSelectHistoryEntry = vi.fn();
  const entry: CorrelationTopologyAnalysis = {
    analysis_id: "topology-h1", workspace: "ws1", status: "completed",
    repos: ["api-gateway", "user-svc"], created_at: "2026-09-03T06:22:00Z",
  };
  render(<CorrelationTopologyAnalysisPanel
    analysis={null} starting={false} error={null} logLines={[]}
    historyEntries={[entry]} historyActiveId="topology-h1"
    onSelectHistoryEntry={onSelectHistoryEntry}
    {...baseHandlers}
  />);
  expect(screen.getByTestId("topology-history")).toBeInTheDocument();
  expect(screen.getByText("api-gateway, user-svc")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /api-gateway/ }));
  expect(onSelectHistoryEntry).toHaveBeenCalledWith(entry);
  // 无历史数据源（未传 onSelectHistoryEntry）→ 历史区不渲染
  const bare = render(<CorrelationTopologyAnalysisPanel
    analysis={null} starting={false} error={null} logLines={[]} {...baseHandlers}
  />);
  expect(bare.container.querySelector("[data-testid='topology-history']")).toBeNull();
});
