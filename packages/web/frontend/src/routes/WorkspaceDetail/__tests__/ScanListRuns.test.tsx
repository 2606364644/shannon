import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import { ScanList } from "../ScanList";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

// 组合扫描带版本化 bb_runs（spec 2026-08-14）：latest=run-2 running，run-1 completed。
const combinedWithRuns = {
  scan_id: "s1", scan_type: "whitebox", status: "running", created_at: 1000,
  completed_at: null, vuln_count: 0, is_running: true, workflow_id: "ws-s1",
  combined: true, bb_phase: "running", progress_pct: 70,
  latest_bb_run: "run-2",
  bb_runs: [
    { run_id: "run-1", status: "completed" },
    { run_id: "run-2", status: "running" },
  ],
} as const;

const server = setupServer(
  http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([])),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderList() {
  return render(
    <MemoryRouter initialEntries={["/p/ws"]}>
      <Routes><Route path="/p/:workspace" element={<ScanList />} /></Routes>
    </MemoryRouter>,
  );
}

describe("ScanList 版本化黑盒 run（T15）", () => {
  it("组合任务卡内嵌 run 列表（run_id + 状态）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([combinedWithRuns])),
    );
    renderList();
    expect(await screen.findByText("run-1")).toBeInTheDocument();
    expect(screen.getByText("run-2")).toBeInTheDocument();
  });

  it("纯白盒任务（无 bb_runs）不渲染 run 列表", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([
        { scan_id: "w1", scan_type: "whitebox", status: "completed", created_at: 1000,
          completed_at: 2000, vuln_count: 3, is_running: false, workflow_id: "ws-w1" }])),
    );
    renderList();
    await screen.findByText("ws-w1");
    expect(screen.queryByTestId("nested-runs")).not.toBeInTheDocument();
  });
});
