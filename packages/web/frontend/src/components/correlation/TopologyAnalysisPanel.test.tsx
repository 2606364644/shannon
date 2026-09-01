import { fireEvent, render, screen } from "@testing-library/react";
import { CorrelationTopologyAnalysisPanel } from "./TopologyAnalysisPanel";

it("shows status, cost, cache and manual fallback", () => {
  const onManual = vi.fn();
  const onRetry = vi.fn();
  render(<CorrelationTopologyAnalysisPanel
    analysis={{
      analysis_id: "topology-1", workspace: "ws1", status: "completed", repos: ["a", "b"],
      cache_hit: true, progress: 100,
      usage: { input_tokens: 1, output_tokens: 2, cost_usd: 3, cost_currency: "CNY" },
    }}
    starting={false} error={null}
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
