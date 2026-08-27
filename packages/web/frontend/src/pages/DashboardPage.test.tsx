import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor, fireEvent, cleanup, act } from "@testing-library/react";
import { renderWithSwr } from "@/test/swr-render";
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
  // TileRunRow（2026-08-27 SSE 实时进度）用；useEventSource 已 mock，此处仅拼 URL。
  scanEventsUrl: (ws: string, scanId: string) => `/api/workspaces/${ws}/scans/${scanId}/events`,
}));

// SSE mock（2026-08-27 列表进度不动修复）：磁贴运行 mini 行进度由 events fold 实时
// 驱动（与 ScanList/详情页同 dashboardReducer 口径）；默认空数组（等价现状）。
const { sseState } = vi.hoisted(() => ({
  sseState: { events: [] as unknown[] },
}));
vi.mock("@/api/useEventSource", () => ({
  useEventSource: () => ({ events: sseState.events, status: "closed" as const }),
}));

const mockScans = [
  { scan_id: "s1", scan_type: "whitebox", status: "running", created_at: 100, vuln_count: 1,
    is_running: true, workspace: "ws-a", repo: "frontend", total_cost_usd: 0.1, progress_pct: 42 },
  { scan_id: "s2", scan_type: "whitebox", status: "completed", created_at: 200, vuln_count: 2,
    is_running: false, workspace: "ws-b", repo: "backend", total_cost_usd: 0.2, completed_at: Math.floor(Date.now() / 1000) },
  { scan_id: "s3", scan_type: "whitebox", status: "failed", created_at: 300, vuln_count: 0,
    is_running: false, workspace: "ws-b", repo: "backend", total_cost_usd: 0.3 },
];
const userUser = { id: 1, username: "alice", role: "user", must_change_password: false };
const userAdmin = { id: 2, username: "root", role: "admin", must_change_password: false };

function renderPage() {
  return renderWithSwr(<MemoryRouter><DashboardPage /></MemoryRouter>);
}

describe("DashboardPage 态势大屏（v2 结构 + v3 横幅重组：横幅 + 工作区磁贴）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiGetMock.mockResolvedValue([]);
    mockUseAuth.mockReturnValue({ user: userUser });
    sseState.events = [];
    return i18n.changeLanguage("zh");
  });
  afterEach(() => cleanup());

  it("横幅：累计发现大数字 + 运营注脚行（运行中/需关注/今日完成/累计成本）+ 最重目标去向", async () => {
    const { listAllScans } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue(mockScans);
    renderPage();
    // 累计发现 1+2+0 = 3（红色大数字）
    const num = await screen.findByTestId("dash-total-vulns");
    expect(num.textContent).toBe("3");
    expect(num.className).toMatch(/text-red/);
    // 规模 context 收尾注脚（v3：「N 个进行中」并入运行中信号不再单列）：repo 标签去重
    // （frontend+backend=2；s3 复用 backend 不重复计）
    expect(screen.getByText("跨 3 次扫描 · 2 个仓库 · 2 个工作区")).toBeInTheDocument();
    // 运营注脚行四信号（「运行中」也出现在 running 磁贴状态字 → 多元素用 getAllByText）
    expect(screen.getAllByText("运行中").length).toBeGreaterThan(0);
    expect(screen.getByText("今日完成")).toBeInTheDocument();
    expect(screen.getByText("累计成本")).toBeInTheDocument();
    expect(screen.getByText("需关注")).toBeInTheDocument();
    // 最重目标去向注记：ws-a=1 / ws-b=2+0=2 → ws-b，点击直达该工作区
    const top = screen.getByRole("link", { name: /最重目标：ws-b · 2 条发现/ });
    expect(top).toHaveAttribute("href", "/p/ws-b");
  });

  it("构成谱带：红色威胁通道（bg-red），不用品牌色 primary（mac 下蓝会弱化危害感）", async () => {
    const { listAllScans } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue([
      { ...mockScans[1], vuln_counts: { xss: 2, ssrf: 1 } },
    ]);
    renderPage();
    // 横幅谱带：分段=漏洞类别（非严重度），整条红色语义通道 + 透明度递减区分分段
    const bar = await screen.findByTestId("dash-composition-bar");
    expect(bar.innerHTML).toContain("bg-red");
    expect(bar.innerHTML).not.toContain("bg-primary");
    // 磁贴谱带同语义
    const tile = await screen.findByTestId("tile-composition-bar");
    expect(tile.innerHTML).toContain("bg-red");
    expect(tile.innerHTML).not.toContain("bg-primary");
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
    // 工作区级别不显失败标志（成功/失败是任务级概念）：ws-b latest=failed 仍无状态字
    expect(screen.queryByText("失败")).not.toBeInTheDocument();
    expect(screen.queryByText("中断")).not.toBeInTheDocument();
  });

  it("磁贴运行 mini 行进度由 SSE 实时事件驱动（fold completed/total，非 progress_pct 恒定值）", async () => {
    const { listAllScans } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue([mockScans[0]]);
    sseState.events = [
      { type: "PhaseEvent", phase: "recon", event: "start", steps: ["step-a", "step-b"], step_intents: ["", ""] },
      { type: "StepEvent", name: "step-a", phase: "recon", event: "complete" },
    ];
    renderPage();
    // 2 步完成 1 步 -> 50%（progress_pct fixture 是 42，fold 优先）
    expect(await screen.findByText("50%")).toBeInTheDocument();
    expect(screen.queryByText("42%")).not.toBeInTheDocument();
  });

  it("SSE 无事件回退 progress_pct（连接建立前）", async () => {
    const { listAllScans } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue([mockScans[0]]);
    sseState.events = [];
    renderPage();
    expect(await screen.findByText("42%")).toBeInTheDocument();
  });

  it("组合扫描白盒段满格 -> 55% 而非 100%（2026-08-28 三阶段加权，黑盒未跑不谎报完成）", async () => {
    const { listAllScans } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue([
      { ...mockScans[0], combined: true, bb_phase: "pending" },
    ]);
    sseState.events = [
      { type: "PhaseEvent", phase: "recon", event: "start", steps: ["a", "b"], step_intents: ["", ""], src: "wb" },
      { type: "StepEvent", name: "a", phase: "recon", event: "complete", src: "wb" },
      { type: "StepEvent", name: "b", phase: "recon", event: "complete", src: "wb" },
    ];
    renderPage();
    expect(await screen.findByText("55%")).toBeInTheDocument();
    expect(screen.queryByText("100%")).not.toBeInTheDocument();
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

  it("全部完成 + 0 发现 → 绿色 all-clear（无最重目标去向 / 无谱带分组线）", async () => {
    const { listAllScans } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue([{ ...mockScans[1], vuln_count: 0 }]);
    renderPage();
    const num = await screen.findByTestId("dash-total-vulns");
    expect(num.className).toMatch(/text-green/);
    expect(screen.getByText("未发现可利用污点路径")).toBeInTheDocument();
    // 一切正常 = 没有「去哪」的问题：去向注记不渲染
    expect(screen.queryByRole("link", { name: /最重目标/ })).not.toBeInTheDocument();
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
