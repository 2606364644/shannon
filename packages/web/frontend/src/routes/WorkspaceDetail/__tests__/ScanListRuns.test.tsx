import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { renderWithSwr } from "@/test/swr-render";
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
  return renderWithSwr(
    <MemoryRouter initialEntries={["/p/ws"]}>
      <Routes><Route path="/p/:workspace" element={<ScanList />} /></Routes>
    </MemoryRouter>,
  );
}

/** 默认收起（2026-08-24）：断言 run 内容前先点展开柄。 */
async function expandRuns() {
  fireEvent.click(await screen.findByRole("button", { name: "展开黑盒 run" }));
}

describe("ScanList 版本化黑盒 run（T15）", () => {
  it("组合任务卡内嵌 run 列表（run_id + 状态，默认收起点柄展开）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([combinedWithRuns])),
    );
    renderList();
    // 默认收起：run 内容不渲染，点展开柄后出现
    await screen.findByText("ws-s1");
    expect(screen.queryByText("run-1")).not.toBeInTheDocument();
    await expandRuns();
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

  // 列化改版（2026-08-24）：run 行是真实 TableRow，与主表同网格——黑盒类型徽标 +
  // completed_at 时间列（任务级 bb_runs[] 条目实际只有 completed_at，started_at 不进条目）。
  it("run 子行列化：黑盒徽标 + completed_at 时间（无 colSpan 大格包裹）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([{
        ...combinedWithRuns,
        bb_runs: [{ run_id: "run-1", status: "completed", completed_at: "2026-08-20T15:03:49+00:00" }],
        latest_bb_run: "run-1",
      }])),
    );
    renderList();
    await expandRuns();
    expect(await screen.findByText("run-1")).toBeInTheDocument();
    // 黑盒类型徽标（主行显「组合」，子行细分「黑盒」）
    expect(screen.getByText("黑盒")).toBeInTheDocument();
    // 时间列（ISO UTC → 本地 MM-DD HH:mm；jsdom 本地时区随环境，只断言含日期段）
    expect(screen.getByText(/08-2\d \d{2}:\d{2}/)).toBeInTheDocument();
  });
});

describe("ScanList 删除单个黑盒 run", () => {
  it("终态 run 删除按钮可点 / 运行中禁用；确认后 DELETE + toast + 刷新列表", async () => {
    const { toast } = await import("sonner");
    const deleted: string[] = [];
    let listCalls = 0;
    server.use(
      http.get("/api/workspaces/:ws/scans", () => { listCalls++; return HttpResponse.json([combinedWithRuns]); }),
      http.delete("/api/workspaces/:ws/scans/:id/blackbox-runs/:run", ({ params }) => {
        deleted.push(String(params.run));
        return HttpResponse.json({ deleted: String(params.run) });
      }),
    );
    renderList();
    await expandRuns();
    await screen.findByText("run-1");
    const delBtns = screen.getAllByRole("button", { name: /删除该 run/ });
    expect(delBtns).toHaveLength(2);          // run-1, run-2 各一个
    expect(delBtns[0]).not.toBeDisabled();    // run-1 completed 可删
    expect(delBtns[1]).toBeDisabled();        // run-2 running 禁用
    fireEvent.click(delBtns[0]);
    fireEvent.click(await screen.findByRole("button", { name: /^确认$/ }));
    await waitFor(() => expect(deleted).toContain("run-1"));
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));  // 删后刷新列表
  });
});
