import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import ScanDetail from "../ScanDetail";
import { ReportTab } from "../ReportTab";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: { id: 1, username: "admin", role: "admin", must_change_password: false, pinned_workspace: null },
    loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn(),
  }),
}));
vi.mock("@/api/useWorkspaces", () => ({
  useWorkspaces: () => ({ data: [], loading: false, lastUpdated: new Date(), error: null, refresh: vi.fn() }),
}));

class FakeES {
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {}
  close() { this.closed = true; }
}
vi.stubGlobal("EventSource", FakeES);

// 组合详情 + 版本化 bb_runs（latest=run-2）。
const combinedWithRuns = {
  scan_type: "whitebox", status: "running", repo_path: "/root/code",
  workflow_id: "ws-s1", combined: true, bb_phase: "running", progress_pct: 80,
  latest_bb_run: "run-2",
  bb_runs: [{ run_id: "run-1", status: "completed" }, { run_id: "run-2", status: "completed" }],
};

const fetched: string[] = [];
const server = setupServer(
  http.get("/api/workspaces/:ws/scans/:id", () => HttpResponse.json(combinedWithRuns)),
  http.get("/api/workspaces/:ws/scans/:id/blackbox-runs/:run/report", ({ params }) => {
    fetched.push(String(params.run));
    return new HttpResponse(`# ${params.run} 融合报告`, { headers: { "content-type": "text/plain" } });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "warn" }));
beforeEach(() => { i18n.changeLanguage("zh"); fetched.length = 0; });
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderDetail(initial = "/p/ws/scans/s1/report") {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="/p/:workspace/scans/:scanId" element={<ScanDetail />}>
          <Route path="report" element={<ReportTab />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("ScanDetail 版本化 run 选择器（T16）", () => {
  it("默认选 latest(run-2)，融合报告读 run-2；切到 run-1 读 run-1", async () => {
    renderDetail();
    // run 选择器渲染（aria-label=选择黑盒 run）
    const sel = await screen.findByRole("combobox", { name: /选择黑盒 run/ });
    expect((sel as HTMLSelectElement).value).toBe("run-2");
    // 默认 combined track → 读 run-2 融合报告
    await waitFor(() => expect(fetched).toContain("run-2"));
    expect(await screen.findByText(/run-2 融合报告/)).toBeInTheDocument();
    // 切到 run-1
    fireEvent.change(sel, { target: { value: "run-1" } });
    await waitFor(() => expect(fetched).toContain("run-1"));
    expect(await screen.findByText(/run-1 融合报告/)).toBeInTheDocument();
  });
});

describe("ScanDetail 加黑盒入口（T17）", () => {
  it("终端态白盒任务显示「加黑盒」→ 确认 POST + toast 成功", async () => {
    const { toast } = await import("sonner");
    server.use(
      http.get("/api/workspaces/:ws/scans/:id", () => HttpResponse.json(
        { scan_type: "whitebox", status: "completed", repo_path: "/code",
          workflow_id: "ws-w1" })),
      http.get("/api/workspaces/:ws/scans/:id/report",
        () => new HttpResponse("# 白盒报告", { headers: { "content-type": "text/plain" } })),
      http.post("/api/workspaces/:ws/scans/:id/blackbox-runs",
        () => HttpResponse.json({ workspace: "ws", scan_id: "s1", run_id: "run-1" }, { status: 202 })),
    );
    renderDetail("/p/ws/scans/s1/report");
    const btn = await screen.findByRole("button", { name: /加黑盒扫描/ });
    fireEvent.click(btn);
    const confirm = await screen.findByRole("button", { name: /^确认$/ });
    fireEvent.click(confirm);
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
  });
});

