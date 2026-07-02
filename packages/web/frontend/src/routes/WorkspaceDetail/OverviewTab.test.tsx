import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { OverviewTab } from "./OverviewTab";

const session = {
  web_url: "", repo_path: "/x", scan_type: "whitebox", status: "running",
  session: { status: "completed" },  // 矛盾
  metrics: {
    total_duration_ms: 5892153, total_cost_usd: 16.29,
    phases: { "pre-recon": { duration_ms: 805974, duration_percentage: 13.68, cost_usd: 3.75, agent_count: 1 } },
    agents: { "injection-vuln": { duration_ms: 434233, cost_usd: 1.15, success: true, attempt_number: 2, model: "GLM-5.2[1m]", error: "api_error_status=429" } },
  },
};
const server = setupServer(http.get("/api/workspaces/:ws", () => HttpResponse.json(session)));
beforeAll(() => server.listen()); afterAll(() => server.close());

describe("OverviewTab", () => {
  it("阶段瀑布渲染 + 大数字", async () => {
    render(<MemoryRouter initialEntries={["/p/ws/overview"]}><Routes><Route path="/p/:workspace/overview" element={<OverviewTab />} /></Routes></MemoryRouter>);
    await waitFor(() => expect(screen.getByText(/pre-recon/)).toBeInTheDocument());
    expect(screen.getByText(/\$16\.29/)).toBeInTheDocument();
    expect(screen.getByText(/13\.68/)).toBeInTheDocument();
  });
  it("status 矛盾标注", async () => {
    render(<MemoryRouter initialEntries={["/p/ws/overview"]}><Routes><Route path="/p/:workspace/overview" element={<OverviewTab />} /></Routes></MemoryRouter>);
    await waitFor(() => expect(screen.getByText(/顶层 running vs session.completed/)).toBeInTheDocument());
  });
  it("重试 agent 标黄（attempt_number=2 + error）", async () => {
    const { container } = render(<MemoryRouter initialEntries={["/p/ws/overview"]}><Routes><Route path="/p/:workspace/overview" element={<OverviewTab />} /></Routes></MemoryRouter>);
    await waitFor(() => expect(container.querySelector(".ev-warn")).toBeInTheDocument());
    expect(container.textContent).toContain("⚠");
  });
});
