import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from "vitest";
import { render, screen, waitFor, cleanup, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { WorkspaceListPage } from "./WorkspaceListPage";
import type { Workspace } from "../api/types";

// 真实形状 fixtures：白盒/黑盒/联动 三态 + 各 status 色
const baseWorkspaces: Workspace[] = [
  { name: "ws-a", scan_type: "whitebox", status: "running", created_at: 0, total_cost_usd: 2.34, total_duration_ms: 2530000, vuln_count: 14 },
  { name: "ws-failed", scan_type: "whitebox", status: "failed", created_at: 0, total_cost_usd: 0.5, total_duration_ms: 60000, vuln_count: 0 },
  { name: "ws-corr", scan_type: "correlation", status: "running", created_at: 0,
    links: { child_workspaces: ["ws-a", "ws-b"] } },
];

const server = setupServer(
  http.get("/api/workspaces", () => HttpResponse.json(baseWorkspaces)),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  cleanup();
  // 防止 fake timers 跨测试泄漏
  if (vi.isFakeTimers()) vi.useRealTimers();
});
afterAll(() => server.close());

function renderPage() {
  return render(<MemoryRouter><WorkspaceListPage /></MemoryRouter>);
}

// 取顶层 workspace 行（排除联动 child 行）
function topRow(name: string) {
  return screen.getAllByText(name, { exact: true })
    .map((n) => n.closest("tr"))
    .find((tr) => tr?.className.includes("ledger-row") && !tr.className.includes("ledger-child"));
}

describe("WorkspaceListPage", () => {
  it("渲染 workspace 行 + 等宽台账表格结构", async () => {
    renderPage();
    await waitFor(() => expect(topRow("ws-a")).toBeDefined());
    // 结构性：等宽 ledger 表格存在 + 表头列
    const table = document.querySelector("table.ledger.mono");
    expect(table).not.toBeNull();
    const headers = Array.from(table!.querySelectorAll("thead th")).map((t) => t.textContent);
    expect(headers).toEqual(["workspace", "status", "type", "vulns", "cost", "time"]);
    // 等宽列：成本 / 时长 渲染
    expect(screen.getByText(/\$2\.34/)).toBeInTheDocument();
    // type 列含 whitebox（多个白盒 ws → 用 AllBy）
    expect(screen.getAllByText(/whitebox/).length).toBeGreaterThanOrEqual(1);
  });

  it("每行渲染 status 色条 + StatusBadge", async () => {
    renderPage();
    await waitFor(() => expect(topRow("ws-a")).toBeDefined());
    // 结构性：status-running 色条 + ledger-row
    const rowA = topRow("ws-a")!;
    expect(rowA.className).toContain("ledger-row");
    expect(rowA.className).toContain("status-running");
    expect(rowA.querySelector(".status-bar.status-running")).not.toBeNull();
    // failed 行红色条
    const rowFailed = topRow("ws-failed")!;
    expect(rowFailed.querySelector(".status-bar.status-failed")).not.toBeNull();
    // StatusBadge 嵌入
    expect(rowA.querySelector(".status-badge")).not.toBeNull();
  });

  it("联动 workspace 展开 子 ws 树 + 🔗", async () => {
    renderPage();
    await waitFor(() => expect(topRow("ws-corr")).toBeDefined());
    // 结构性：联动行带 🔗（🔗 是 td 内文本节点，与 <a> 同级）
    const corrRow = topRow("ws-corr")!;
    expect(corrRow.querySelector("td")?.textContent).toContain("🔗");
    // ws-b 仅作为 child 行出现（不在顶层 ledger-row）
    const wsBChildRow = screen.getAllByText("ws-b", { exact: true })
      .map((n) => n.closest("tr"))
      .find((tr) => tr?.className.includes("ledger-child"));
    expect(wsBChildRow).toBeDefined();
    expect(wsBChildRow?.className).toContain("trace");
    // 子行 colSpan 横跨全表
    expect(wsBChildRow?.querySelector("td")?.getAttribute("colSpan")).toBe("6");
    // ws-a 既是顶层白盒行又是联动子（作为 child 出现一次）
    const wsAChildRow = screen.getAllByText("ws-a", { exact: true })
      .map((n) => n.closest("tr"))
      .find((tr) => tr?.className.includes("ledger-child"));
    expect(wsAChildRow).toBeDefined();
  });

  it("5s 轮询：定时刷新 + unmount 清理 timer", async () => {
    vi.useFakeTimers();
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderPage();
    // fake timer 下推进让初始 fetch resolve（advanceTimersByTimeAsync 同时 flush 微任务）
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(fetchSpy.mock.calls.length).toBeGreaterThanOrEqual(1);
    const callsAfterMount = fetchSpy.mock.calls.length;
    // 推进 5s 触发轮询 fetch
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetchSpy.mock.calls.length).toBeGreaterThan(callsAfterMount);
    const callsAfterTick = fetchSpy.mock.calls.length;
    // unmount 后再推进，不应再触发 fetch（timer 已清理）
    cleanup();
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
    expect(fetchSpy.mock.calls.length).toBe(callsAfterTick);
    fetchSpy.mockRestore();
  });

  it("空列表渲染空态", async () => {
    server.use(http.get("/api/workspaces", () => HttpResponse.json([])));
    renderPage();
    await waitFor(() => expect(screen.getByText(/no workspaces/i)).toBeInTheDocument());
    expect(screen.queryByText("ws-a")).not.toBeInTheDocument();
  });
});
