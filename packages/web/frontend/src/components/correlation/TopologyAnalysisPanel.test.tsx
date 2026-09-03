import { fireEvent, render, screen } from "@testing-library/react";
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

it("hides audit trail when idle with no lines", () => {
  const { container } = render(<CorrelationTopologyAnalysisPanel
    analysis={null} starting={false} error={null} logLines={[]}
    onStart={vi.fn()} onRetry={vi.fn()} onCancel={vi.fn()} onManual={vi.fn()}
  />);
  expect(container.querySelector(".font-mono")).toBeNull();
});
