import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
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
          workflow_id: "ws-w1", web_url: "http://t" })),
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

// 组合详情 + 版本化 bb_runs（latest=run-1，run-1 因 provider 配置缺失失败）。
const combinedFailedRun = {
  scan_type: "whitebox", status: "failed", repo_path: "/root/code",
  workflow_id: "ws-s1", combined: true, bb_phase: "failed", progress_pct: 50,
  latest_bb_run: "run-1",
  bb_runs: [{
    run_id: "run-1", status: "failed",
    reason: "workspace provider config incomplete; missing: SUPERNOVA_OPENAI_API_KEY",
  }],
};

describe("ScanDetail 失败 run 可见性（失败原因前端可见化）", () => {
  it("failed run：selector 显「失败」+ 顶部失败横幅 + 工作区设置链接", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:id", () => HttpResponse.json(combinedFailedRun)),
      // run 失败 → 无报告，404（showRunFailure 优先于 ErrorState 显横幅）。
      http.get("/api/workspaces/:ws/scans/:id/blackbox-runs/:run/report",
        () => new HttpResponse("", { status: 404 })),
    );
    renderDetail("/p/ws/scans/s1/report");
    const sel = await screen.findByRole("combobox", { name: /选择黑盒 run/ });
    // option 带状态后缀「失败」（status 优先于「最新」）。
    expect((sel as HTMLSelectElement).options[0].text).toContain("失败");
    // 失败横幅（ScanDetail 顶部；ReportTab combined tab 也可能各渲染一个 → findAll）。
    expect(await screen.findAllByText(/工作区 LLM 凭据未配置/)).not.toHaveLength(0);
    // 引导链接指向工作区设置。
    const link = screen.getAllByRole("link", { name: /前往工作区设置/ })[0];
    expect(link.getAttribute("href")).toContain("/p/ws/settings");
  });

  it("completed run：不显失败横幅", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:id", () => HttpResponse.json(combinedWithRuns)),
      http.get("/api/workspaces/:ws/scans/:id/blackbox-runs/:run/report",
        () => new HttpResponse("# ok", { headers: { "content-type": "text/plain" } })),
    );
    renderDetail("/p/ws/scans/s1/report");
    await screen.findByRole("combobox", { name: /选择黑盒 run/ });
    await waitFor(() => {
      expect(screen.queryByText(/工作区 LLM 凭据未配置/)).not.toBeInTheDocument();
      expect(screen.queryByTestId("run-failure-banner")).not.toBeInTheDocument();
    });
  });
});

describe("ScanDetail 删除单个黑盒 run", () => {
  it("终态 run 显示删除按钮，确认后 DELETE + toast 成功", async () => {
    const { toast } = await import("sonner");
    const deleted: string[] = [];
    server.use(
      http.get("/api/workspaces/:ws/scans/:id", () => HttpResponse.json(combinedWithRuns)),
      http.get("/api/workspaces/:ws/scans/:id/blackbox-runs/:run/report",
        () => new HttpResponse("# ok", { headers: { "content-type": "text/plain" } })),
      http.delete("/api/workspaces/:ws/scans/:id/blackbox-runs/:run", ({ params }) => {
        deleted.push(String(params.run));
        return HttpResponse.json({ deleted: String(params.run) });
      }),
    );
    renderDetail("/p/ws/scans/s1/report?run=run-1");
    fireEvent.click(await screen.findByRole("button", { name: /删除该 run/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^确认$/ }));
    await waitFor(() => expect(deleted).toContain("run-1"));
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
  });

  it("运行中 run 删除按钮禁用", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:id", () => HttpResponse.json({
        ...combinedWithRuns,
        bb_runs: [{ run_id: "run-1", status: "running" }],
        latest_bb_run: "run-1",
      })),
      http.get("/api/workspaces/:ws/scans/:id/blackbox-runs/:run/report",
        () => new HttpResponse("# ok", { headers: { "content-type": "text/plain" } })),
    );
    renderDetail("/p/ws/scans/s1/report?run=run-1");
    const delBtn = await screen.findByRole("button", { name: /删除该 run/ });
    expect(delBtn).toBeDisabled();
  });
});

