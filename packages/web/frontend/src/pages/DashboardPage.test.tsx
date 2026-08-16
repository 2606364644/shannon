import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import i18n from "@/i18n";
import { DashboardPage } from "./DashboardPage";

// mock listAllScans（跨 ws 聚合）+ useAuth（admin/user gate）+ apiGet（admin 无 ws 空态判定）。
// v2 重设计（2026-08-16）：概览 = 只读态势大屏（横幅 + 磁贴），取消等操作全部在工作区页，
// cancelScan 不再被 Dashboard 引用（mock 保留供 CreateWorkspaceDialog 链路）。
const { mockUseAuth, apiGetMock } = vi.hoisted(() => ({ mockUseAuth: vi.fn(), apiGetMock: vi.fn() }));
vi.mock("@/auth/AuthContext", () => ({ useAuth: () => mockUseAuth() }));
vi.mock("@/api/client", () => ({
  listAllScans: vi.fn(),
  cancelScan: vi.fn(),
  createWorkspace: vi.fn(),
  apiGet: apiGetMock,
}));

const mockScans = [
  { scan_id: "s1", scan_type: "whitebox", status: "running", created_at: 100, vuln_count: 1,
    is_running: true, workspace: "ws-a", total_cost_usd: 0.1, progress_pct: 42 },
  { scan_id: "s2", scan_type: "whitebox", status: "completed", created_at: 200, vuln_count: 2,
    is_running: false, workspace: "ws-b", total_cost_usd: 0.2, completed_at: Math.floor(Date.now() / 1000) },
  { scan_id: "s3", scan_type: "whitebox", status: "failed", created_at: 300, vuln_count: 0,
    is_running: false, workspace: "ws-b", total_cost_usd: 0.3 },
];
const userUser = { id: 1, username: "alice", role: "user", must_change_password: false };
const userAdmin = { id: 2, username: "root", role: "admin", must_change_password: false };

function renderPage() {
  return render(<MemoryRouter><DashboardPage /></MemoryRouter>);
}

describe("DashboardPage v2 态势大屏（横幅 + 工作区磁贴）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiGetMock.mockResolvedValue([]);
    mockUseAuth.mockReturnValue({ user: userUser });
    return i18n.changeLanguage("zh");
  });
  afterEach(() => cleanup());

  it("横幅：累计发现大数字 + 运营指标（运行中/今日完成/累计成本/需关注）", async () => {
    const { listAllScans } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue(mockScans);
    renderPage();
    // 累计发现 1+2+0 = 3（红色大数字）
    const num = await screen.findByTestId("dash-total-vulns");
    expect(num.textContent).toBe("3");
    expect(num.className).toMatch(/text-red/);
    // 横幅运营指标四格（「运行中」也出现在 running 磁贴状态字 → 多元素用 getAllByText）
    expect(screen.getAllByText("运行中").length).toBeGreaterThan(0);
    expect(screen.getByText("今日完成")).toBeInTheDocument();
    expect(screen.getByText("累计成本")).toBeInTheDocument();
    expect(screen.getByText("需关注")).toBeInTheDocument();
  });

  it("磁贴：按工作区分组渲染（ws-a/ws-b），运行中扫描进度融进磁贴", async () => {
    const { listAllScans } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue(mockScans);
    renderPage();
    expect(await screen.findByTestId("ws-tile-ws-a")).toBeInTheDocument();
    expect(screen.getByTestId("ws-tile-ws-b")).toBeInTheDocument();
    // ws-a 有运行中 s1：mini 行显 42% + 白盒段标签
    expect(screen.getByText("42%")).toBeInTheDocument();
    expect(screen.getByTestId("tile-run-meta-s1").textContent).toContain("白盒");
    // 磁贴 meta：扫描数
    expect(screen.getByText("1 扫描")).toBeInTheDocument();
    expect(screen.getByText("2 扫描")).toBeInTheDocument();
  });

  it("无扫描表格（明细与操作全部在工作区页，两页零结构重叠）", async () => {
    const { listAllScans } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue(mockScans);
    renderPage();
    await screen.findByTestId("ws-tile-ws-a");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("全部完成 + 0 发现 → 绿色 all-clear", async () => {
    const { listAllScans } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue([{ ...mockScans[1], vuln_count: 0 }]);
    renderPage();
    const num = await screen.findByTestId("dash-total-vulns");
    expect(num.className).toMatch(/text-green/);
    expect(screen.getByText("未发现可利用污点路径")).toBeInTheDocument();
  });

  it("empty state when no scans", async () => {
    const { listAllScans } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(screen.getByText("还没有扫描")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: /新建扫描/ })).toHaveAttribute("href", "/scan/new");
  });

  it("首次加载失败渲染错误态而非空态，重试后恢复", async () => {
    const { listAllScans } = await import("@/api/client");
    (listAllScans as any).mockRejectedValueOnce(new Error("boom"));
    renderPage();
    await waitFor(() => expect(screen.getByText("加载失败")).toBeInTheDocument());
    expect(screen.getByText(/Dashboard 加载失败：boom/)).toBeInTheDocument();
    expect(screen.queryByText("还没有扫描")).not.toBeInTheDocument();
    // 点重试 -> 重新拉取并渲染磁贴
    (listAllScans as any).mockResolvedValue(mockScans);
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(screen.getByTestId("ws-tile-ws-a")).toBeInTheDocument());
  });

  it("有运行中扫描时每 10s 自动轮询；全部完成后停止", async () => {
    vi.useFakeTimers();
    try {
      const { listAllScans } = await import("@/api/client");
      // s1 running → 轮询持续；之后 s1 完成 → 轮询停
      (listAllScans as any)
        .mockResolvedValueOnce(mockScans)
        .mockResolvedValueOnce(mockScans)
        .mockResolvedValue([{ ...mockScans[1] }]);
      renderPage();
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(screen.getByText("42%")).toBeInTheDocument();
      expect(listAllScans).toHaveBeenCalledTimes(1);
      await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
      expect(listAllScans).toHaveBeenCalledTimes(2);
      expect(screen.getByText("42%")).toBeInTheDocument();
      // 第三次返回 s1 已完成 → hasRunning 翻 false，轮询清除
      await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
      expect(listAllScans).toHaveBeenCalledTimes(3);
      await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
      expect(listAllScans).toHaveBeenCalledTimes(3);
      expect(screen.queryByText("42%")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("DashboardPage admin 空态", () => {
  beforeEach(() => {
    mockUseAuth.mockReturnValue({ user: userAdmin });
  });

  it("admin 无任何工作区：空态渲染「新建工作区」入口（解锁创建——入口原本只在 ws 内 Switcher，无 ws 时进不去）", async () => {
    const { listAllScans, apiGet } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue([]); // 无扫描
    (apiGet as any).mockResolvedValue([]); // 无工作区
    renderPage();
    await waitFor(() => expect(screen.getByRole("button", { name: /新建工作区/ })).toBeInTheDocument());
    expect(screen.queryByRole("link", { name: /新建扫描/ })).not.toBeInTheDocument();
  });

  it("admin 有工作区但无扫描：空态仍是「新建扫描」（不误显创建入口）", async () => {
    const { listAllScans, apiGet } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue([]);
    (apiGet as any).mockResolvedValue([{ name: "ws-x" }]); // 有工作区
    renderPage();
    await waitFor(() => expect(screen.getByRole("link", { name: /新建扫描/ })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /新建工作区/ })).not.toBeInTheDocument();
  });
});
