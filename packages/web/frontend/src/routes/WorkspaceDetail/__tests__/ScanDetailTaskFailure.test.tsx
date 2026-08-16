import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
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

// precheck（t0 认证预验证）失败：无 bb_runs，只有任务级 bb_reason + verdict 详情
//（2026-08-16 NodeGoat：登录地址不可达 → run 级横幅永不触发，用户只看到「失败」）。
const precheckFailed = {
  scan_type: "whitebox", status: "failed", repo_path: "/root/code",
  workflow_id: "ws-s1", combined: true, bb_phase: "failed", progress_pct: 0,
  bb_reason: "auth_failed",
  bb_failure_point: "username_or_password",
  bb_failure_detail: "Target unreachable: TCP connect to 192.168.100.206:4000 refused",
  bb_runs: [], latest_bb_run: null,
};

const server = setupServer(
  http.get("/api/workspaces/:ws/scans/:id", () => HttpResponse.json(precheckFailed)),
  http.get("/api/workspaces/:ws/scans/:id/report",
    () => new HttpResponse("# ok", { headers: { "content-type": "text/plain" } })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "warn" }));
beforeEach(() => { i18n.changeLanguage("zh"); });
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

describe("ScanDetail 任务级失败横幅（precheck 失败可见性）", () => {
  it("auth_failed + 无 run：显示分类标题 + 原始 detail + 认证配置链接", async () => {
    renderDetail();
    // 分类标题（reason=auth_failed → authFailed 类别）
    expect(await screen.findByText(/目标认证预验证失败/)).toBeInTheDocument();
    // verdict 原文（mono 详情块）
    expect(await screen.findByTestId("run-failure-detail")).toHaveTextContent(
      /TCP connect to 192\.168\.100\.206:4000 refused/);
    // 引导链接指向认证配置页（用户改 login_url 的地方）
    const link = screen.getByRole("link", { name: /前往认证配置/ });
    expect(link.getAttribute("href")).toContain("/p/ws/auth-profiles");
  });

  it("无 bb_failure_detail（历史失败扫描）：横幅仍显示，只是没有详情块", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:id", () => HttpResponse.json({
        ...precheckFailed, bb_failure_point: null, bb_failure_detail: null })),
    );
    renderDetail();
    expect(await screen.findByText(/目标认证预验证失败/)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByTestId("run-failure-detail")).not.toBeInTheDocument();
    });
  });

  it("run 级横幅已在展示时不再重复渲染任务级横幅", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:id", () => HttpResponse.json({
        ...precheckFailed,
        latest_bb_run: "run-1",
        bb_runs: [{
          run_id: "run-1", status: "failed",
          reason: "workspace provider config incomplete; missing: SUPERNOVA_OPENAI_API_KEY",
        }],
      })),
      http.get("/api/workspaces/:ws/scans/:id/blackbox-runs/:run/report",
        () => new HttpResponse("", { status: 404 })),
    );
    renderDetail();
    // run 级 providerMissing 横幅显示（ScanDetail 顶部 + ReportTab 各一个 → findAll）
    expect(await screen.findAllByText(/工作区 LLM 凭据未配置/)).not.toHaveLength(0);
    // 任务级 authFailed 横幅不重复出现
    await waitFor(() => {
      expect(screen.queryByText(/目标认证预验证失败/)).not.toBeInTheDocument();
    });
  });

  it("非失败状态：无任务级横幅", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:id", () => HttpResponse.json({
        ...precheckFailed, status: "running", bb_phase: "running",
        bb_reason: null })),
    );
    renderDetail();
    await waitFor(() => {
      expect(screen.queryByTestId("run-failure-banner")).not.toBeInTheDocument();
    });
  });
});
