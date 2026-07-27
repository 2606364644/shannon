import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import i18n from "@/i18n";
import { DashboardPage } from "./DashboardPage";

// mock listAllScans（跨 ws 聚合）+ cancelScan（admin 操作列）+ useAuth（admin/user gate）。
const { mockUseAuth } = vi.hoisted(() => ({ mockUseAuth: vi.fn() }));
vi.mock("@/auth/AuthContext", () => ({ useAuth: () => mockUseAuth() }));
vi.mock("@/api/client", () => ({
  listAllScans: vi.fn(),
  cancelScan: vi.fn(),
}));

const mockScans = [
  { scan_id: "s1", scan_type: "whitebox", status: "running", created_at: 100, vuln_count: 1, is_running: true, workspace: "ws-a", total_cost_usd: 0.1 },
  { scan_id: "s2", scan_type: "blackbox", status: "completed", created_at: 200, vuln_count: 2, is_running: false, workspace: "ws-b", total_cost_usd: 0.2 },
];
const userUser = { id: 1, username: "alice", role: "user", must_change_password: false };
const userAdmin = { id: 2, username: "root", role: "admin", must_change_password: false };

function renderPage() {
  return render(<MemoryRouter><DashboardPage /></MemoryRouter>);
}

describe("DashboardPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // 默认普通用户（admin 操作列不渲染）；admin describe 内 beforeEach 覆写为 admin。
    mockUseAuth.mockReturnValue({ user: userUser });
    // jsdom navigator.language 默认 en,LanguageDetector 把 i18n 切到 en;
    // 状态筛选选项断言用中文「已完成」,逐测试钉回 zh。
    return i18n.changeLanguage("zh");
  });
  afterEach(() => cleanup());

  it("renders scan table with workspace column", async () => {
    const { listAllScans } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue(mockScans);
    renderPage();
    // s1 是 running,同时出现在顶部 running 卡片和表格中 -> getAllByText
    await waitFor(() => expect(screen.getAllByText("s1").length).toBeGreaterThan(0));
    // 工作区列:ws-a / ws-b 都在表格的 Link 文本里(running 卡片里是「工作区: ws-a」整段,不撞 exact 匹配)
    expect(screen.getByText("ws-a")).toBeInTheDocument();
    expect(screen.getByText("ws-b")).toBeInTheDocument();
  });

  it("status filter narrows results", async () => {
    const { listAllScans } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue(mockScans);
    renderPage();
    await waitFor(() => expect(screen.getAllByText("s1").length).toBeGreaterThan(0));

    // ScanFilters 的 combobox 顺序 = status[0] / type[1] / time[2](keyword 是 Input 非 combobox)。
    // brief 原写 statusSelects[1] 实际命中 type,错误;改用 aria-label 精确定位 status 筛选。
    // 用 fireEvent.click 而非 mouseDown:本 jsdom 版本 mouseDown 触发 pointerCapture 未实现错误
    // (见 ScanNewPage.test 同款注释)。click 是已验证可复现的姿势。
    const statusTrigger = screen.getByRole("combobox", { name: /状态筛选/ });
    fireEvent.click(statusTrigger);
    const opt = await screen.findByRole("option", { name: "已完成" }, { timeout: 1000 });
    fireEvent.click(opt);

    // 选 completed -> 只剩 s2(completed);s1(running) 从 running 卡片 + 表格整体消失
    await waitFor(() => {
      expect(screen.queryByText("s1")).not.toBeInTheDocument();
      expect(screen.getByText("s2")).toBeInTheDocument();
    });
  });

  it("empty state when no scans", async () => {
    const { listAllScans } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue([]);
    renderPage();
    // Empty 空态:title「还没有扫描」+ 新建扫描按钮。用 exact 标题断言空态渲染
    // (brief 原正则 /还没有扫描|新建扫描/ 同时命中 title 和按钮文本 -> getByText 多元素抛错,
    //  改 exact 单匹配)。
    await waitFor(() => expect(screen.getByText("还没有扫描")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: /新建扫描/ })).toHaveAttribute("href", "/scan/new");
  });
});

describe("DashboardPage admin 操作列（spec 2026-07-27 下线 WorkspaceListPage：取消并入 Dashboard）", () => {
  beforeEach(() => {
    mockUseAuth.mockReturnValue({ user: userAdmin });
  });

  it("admin: running 行渲染操作列取消按钮，completed 行无按钮", async () => {
    const { listAllScans } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue(mockScans);
    renderPage();
    await waitFor(() => expect(screen.getAllByText("s1").length).toBeGreaterThan(0));
    // s1(running, ws-a)有取消按钮；s2(completed)操作列无按钮
    expect(screen.queryByTestId("dashboard-cancel-scan-s1")).toBeInTheDocument();
    expect(screen.queryByTestId("dashboard-cancel-scan-s2")).not.toBeInTheDocument();
  });

  it("admin: 点取消→确认 Dialog→调 cancelScan(ws, scanId) per-scan", async () => {
    const { listAllScans, cancelScan } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue(mockScans);
    (cancelScan as any).mockResolvedValue({ cancelled: "s1", via: "signal" });
    renderPage();
    await waitFor(() => expect(screen.getAllByText("s1").length).toBeGreaterThan(0));
    fireEvent.click(screen.getByTestId("dashboard-cancel-scan-s1"));
    // Dialog 确认按钮（common.confirm = 确认）
    const confirm = await screen.findByRole("button", { name: /^确认$/ });
    fireEvent.click(confirm);
    await waitFor(() => expect(cancelScan).toHaveBeenCalledWith("ws-a", "s1"));
  });

  it("普通用户:无操作列（admin gate，体验不降级）", async () => {
    mockUseAuth.mockReturnValue({ user: userUser });
    const { listAllScans } = await import("@/api/client");
    (listAllScans as any).mockResolvedValue(mockScans);
    renderPage();
    await waitFor(() => expect(screen.getAllByText("s1").length).toBeGreaterThan(0));
    expect(screen.queryByTestId("dashboard-cancel-scan-s1")).not.toBeInTheDocument();
  });
});
