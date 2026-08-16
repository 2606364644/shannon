import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse, delay } from "msw";
import { SWRConfig } from "swr";
import i18n from "@/i18n";
import ScanDetail from "./ScanDetail";

// useEventSource mock（可控 events）：回归测试注入历史回放含 scan_end；其余测试空事件
// （等价 jsdom 无 EventSource 时的 no-op）。
const sse = vi.hoisted(() => ({
  events: [] as Array<Record<string, unknown>>,
  status: "open" as string,
}));
vi.mock("@/api/useEventSource", () => ({
  useEventSource: () => ({ events: sse.events, status: sse.status, lastEventId: undefined }),
}));

const server = setupServer(
  http.get("/api/workspaces/:ws/scans/:scanId", () =>
    HttpResponse.json({ status: "running", scan_type: "whitebox", repo_path: "/root/code" }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => { i18n.changeLanguage("zh"); sse.events = []; sse.status = "open"; });
afterEach(() => { server.resetHandlers(); cleanup(); i18n.changeLanguage("zh"); });
afterAll(() => server.close());

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      {/* SWR 迁移适配（spec §6.5）：独立 cache，防全局缓存跨测试污染
          （onScanEnd 死循环回归测试依赖 server.use 换 handler 后真的重新 fetch）。 */}
      <SWRConfig value={{ provider: () => new Map() }}>
        <Routes>
          <Route path="/p/:workspace/scans/:scanId" element={<ScanDetail />}>
            <Route index element={<div>default-content</div>} />
            <Route path="overview" element={<div>ov-content</div>} />
            <Route path="report" element={<div>rp-content</div>} />
            <Route path="deliverables" element={<div>dl-content</div>} />
            <Route path="logs" element={<div>lg-content</div>} />
            <Route path="live" element={<div>lv-content</div>} />
          </Route>
        </Routes>
      </SWRConfig>
    </MemoryRouter>,
  );
}

describe("ScanDetail per-scan 视图", () => {
  it("渲染 scan_id + 5 scan tabs + 返回 ws 链接", () => {
    renderAt("/p/ws/scans/s1/live");
    expect(screen.getByText("s1")).toBeInTheDocument();
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(screen.getAllByRole("tab")).toHaveLength(5);
    // 返回 ws 概览链接（/p/ws）
    expect(screen.getByRole("link", { name: /返回工作区/ }).getAttribute("href")).toBe("/p/ws");
  });

  it("当前 tab aria-selected（live）", () => {
    renderAt("/p/ws/scans/s1/live");
    expect(screen.getByRole("tab", { name: "实时" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "概览" })).toHaveAttribute("aria-selected", "false");
  });

  it("点 tab 触发导航", () => {
    renderAt("/p/ws/scans/s1/live");
    fireEvent.mouseDown(screen.getByRole("tab", { name: "报告" }));
    expect(screen.getByText("rp-content")).toBeInTheDocument();
  });

  it("scan header 显 status/scan_type/repo_path", async () => {
    const { container } = renderAt("/p/ws/scans/s1/live");
    await waitFor(() => expect(screen.getByText("whitebox")).toBeInTheDocument());
    expect(screen.getByText("/root/code")).toBeInTheDocument();
    expect(container.querySelector("[title='running']")).toBeInTheDocument();
  });

  it("i18n 英文 tab 标签 + 返回 ws 链接", () => {
    i18n.changeLanguage("en");
    renderAt("/p/ws/scans/s1/live");
    expect(screen.getByRole("tab", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Report" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Live" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Back to workspace/ })).toBeInTheDocument();
  });
});

// === onScanEnd 静默刷新（防死循环回归，2026-08-17 报告页疯狂刷新 bug）===
// 终态扫描的事件历史回放必含 scan_end。若 onScanEnd 走带 loading 的 load：loading 翻转
// 卸载 ScanProgressOverview → endedFor ref 销毁 → 重挂载后回放的 scan_end 再次触发
// onScanEnd → getScan 循环。回归断言：scan_end 只静默重拉一次（共 2 次 getScan），稳定。
describe("ScanDetail onScanEnd 静默刷新", () => {
  it("历史回放含 scan_end：只补拉一次 meta，不进入 getScan 死循环", async () => {
    let scanGets = 0;
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId", async () => {
        scanGets += 1;
        // 真实网络延迟：让 setLoading(true) 先渲染、卸载 ScanProgressOverview——
        // 无延迟时 MSW 同微任务解析，true/false 两次 setState 被批处理合并，卸载路径走不到。
        await delay(30);
        return HttpResponse.json({ status: "completed", scan_type: "whitebox" });
      }),
    );
    sse.events = [{ ts: "2026-08-17T00:00:00.000Z", type: "scan_end" }];
    renderAt("/p/ws/scans/s1/report");
    // 初次加载完成 → 进度概览挂载
    await waitFor(() => expect(screen.getByTestId("scan-progress-overview")).toBeInTheDocument());
    // scan_end 触发一次静默重拉（getScan #2），随后稳定不再增长（死循环则持续增长）
    await waitFor(() => expect(scanGets).toBe(2));
    await new Promise((r) => setTimeout(r, 300));
    expect(scanGets).toBe(2);
    // 进度概览全程不卸载（死循环的可见症状：卸载/重挂载闪烁）
    expect(screen.getByTestId("scan-progress-overview")).toBeInTheDocument();
  });
});

// === 加黑盒入口门控（后端 422 兜底，前端先拦免空跑）===
// 纯白盒任务无目标 URL（黑盒无目标可打，workflow 不 fail-fast 会空跑一轮 LLM）。
// 2026-08-17 根因修后：run 在跑期间任务级 status=running（后端 _add_blackbox_run 上浮），
// 按钮随 status 自然隐藏；「status=completed + run 在跑」只作为 legacy 状态兜底（禁用）。
// cancelled（取消过手动 run，白盒产物完好）→ 按钮仍可用（后端 deliverables_ready 兜底）。
describe("ScanDetail 加黑盒入口门控", () => {
  it("纯白盒（无 web_url）→ 按钮禁用 + title 提示无目标 URL", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId", () =>
        HttpResponse.json({ status: "completed", scan_type: "whitebox", web_url: "" })),
    );
    renderAt("/p/ws/scans/s1/report");
    const btn = await screen.findByRole("button", { name: /加黑盒扫描/ });
    expect(btn).toBeDisabled();
    expect(btn.getAttribute("title")).toContain("目标 URL");
  });

  it("run 进行中（任务级 status=running，新口径）→ 按钮隐藏", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId", () =>
        HttpResponse.json({
          status: "running", scan_type: "whitebox", web_url: "http://t",
          combined: true, latest_bb_run: "run-1",
          bb_runs: [{ run_id: "run-1", status: "running" }],
        })),
    );
    renderAt("/p/ws/scans/s1/report");
    await screen.findByText("s1"); // header 就绪
    expect(screen.queryByRole("button", { name: /加黑盒扫描/ })).not.toBeInTheDocument();
  });

  it("run 进行中 + 任务级停在 completed（legacy 状态）→ 按钮禁用 + title 提示等待", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId", () =>
        HttpResponse.json({
          status: "completed", scan_type: "whitebox", web_url: "http://t",
          combined: true, latest_bb_run: "run-1",
          bb_runs: [{ run_id: "run-1", status: "running" }],
        })),
    );
    renderAt("/p/ws/scans/s1/report");
    const btn = await screen.findByRole("button", { name: /加黑盒扫描/ });
    expect(btn).toBeDisabled();
    expect(btn.getAttribute("title")).toContain("进行中");
  });

  it("有目标 URL 且 run 终态 → 按钮可点", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId", () =>
        HttpResponse.json({
          status: "completed", scan_type: "whitebox", web_url: "http://t",
          combined: true, latest_bb_run: "run-1",
          bb_runs: [{ run_id: "run-1", status: "completed" }],
        })),
    );
    renderAt("/p/ws/scans/s1/report");
    const btn = await screen.findByRole("button", { name: /加黑盒扫描/ });
    await waitFor(() => expect(btn).toBeEnabled());
  });

  it("任务级 cancelled（取消过手动 run，白盒产物完好）→ 按钮可点（可再加黑盒）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans/:scanId", () =>
        HttpResponse.json({
          status: "cancelled", scan_type: "whitebox", web_url: "http://t",
          combined: true, latest_bb_run: "run-1",
          bb_runs: [{ run_id: "run-1", status: "cancelled" }],
        })),
    );
    renderAt("/p/ws/scans/s1/report");
    const btn = await screen.findByRole("button", { name: /加黑盒扫描/ });
    await waitFor(() => expect(btn).toBeEnabled());
  });
});
