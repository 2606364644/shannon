import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { SWRConfig } from "swr";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import ScanDetail from "../ScanDetail";
import { ReportTab } from "../ReportTab";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

class FakeES {
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {}
  close() { this.closed = true; }
}
vi.stubGlobal("EventSource", FakeES);

// 假完成形态（2026-08-27 NodeGoat-20260827-152204 事故）：combined 扫描死于 precheck
// （authcheck 3 次超时 + web/worker 中途重启），孤儿对账误标 completed——status=completed
// 但白盒从未启动（expected 6 / completed 0），报告全空且用户看不到任何异常。
const fakeCompleted = {
  scan_type: "whitebox", status: "completed", repo_path: "/root/code",
  workflow_id: "ws-s1", combined: true, bb_phase: "precheck", progress_pct: 100,
  completed_agents: [], expected_agents: { whitebox: 6 },
  bb_runs: [], latest_bb_run: null,
};

const server = setupServer(
  http.get("/api/workspaces/:ws/scans/:id", () => HttpResponse.json(fakeCompleted)),
  http.get("/api/workspaces/:ws/scans/:id/report",
    () => new HttpResponse("# ok", { headers: { "content-type": "text/plain" } })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "warn" }));
beforeEach(() => { i18n.changeLanguage("zh"); });
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderDetail(initial = "/p/ws/scans/s1/report") {
  return render(
    <SWRConfig value={{ provider: () => new Map() }}>
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="/p/:workspace/scans/:scanId" element={<ScanDetail />}>
          <Route path="report" element={<ReportTab />} />
        </Route>
      </Routes>
    </MemoryRouter>,
    </SWRConfig>
  );
}

describe("ScanDetail 假完成警告横幅（completed 但零 agent 产出）", () => {
  it("completed + combined + expected>0 + completed=0：显示警告横幅（含 0/6 进度）", async () => {
    renderDetail();
    const banner = await screen.findByTestId("fake-completed-banner");
    expect(banner).toHaveTextContent(/没有任何 agent 产出/);
    expect(banner).toHaveTextContent(/0\/6/);
  });

  it("正常完成（completed_agents 非空）：不渲染横幅", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:id", () => HttpResponse.json({
        ...fakeCompleted,
        bb_phase: "completed",
        completed_agents: ["pre-recon", "recon"],
      })),
    );
    renderDetail();
    await waitFor(() => {
      expect(screen.queryByTestId("fake-completed-banner")).not.toBeInTheDocument();
    });
  });

  it("非 combined（纯白盒）：不渲染横幅（零回归）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:id", () => HttpResponse.json({
        ...fakeCompleted, combined: false })),
    );
    renderDetail();
    await waitFor(() => {
      expect(screen.queryByTestId("fake-completed-banner")).not.toBeInTheDocument();
    });
  });
});
