import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup, act, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import { Toaster } from "@/components/ui/sonner";
import { WorkspaceListPage } from "./WorkspaceListPage";
import type { Workspace } from "../api/types";

const baseWorkspaces: Workspace[] = [
  { name: "ws-a", scan_type: "whitebox", status: "running", created_at: 1780000000, total_cost_usd: 2.34, total_duration_ms: 2530000, vuln_count: 14, is_correlation: false },
  { name: "ws-failed", scan_type: "blackbox", status: "failed", created_at: 1780000100, total_cost_usd: 0.5, total_duration_ms: 60000, vuln_count: 0, is_correlation: false },
  { name: "ws-corr", scan_type: "correlation", status: "completed", created_at: 1780000200, is_correlation: true, links: { child_workspaces: ["ws-child1"] } },
];

const server = setupServer(
  http.get("/api/workspaces", () => HttpResponse.json(baseWorkspaces)),
  http.delete("/api/workspaces/:ws", ({ params }) => HttpResponse.json({ deleted: params.ws })),
  http.delete("/api/scan/:ws", ({ params }) => HttpResponse.json({ cancelled: params.ws })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
// jsdom navigator.language 默认 en,LanguageDetector 会把 i18n 切到 en;迁移后断言依赖中文渲染,逐测试钉回 zh。
beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderPage() {
  return render(<MemoryRouter><WorkspaceListPage /><Toaster /></MemoryRouter>);
}

describe("WorkspaceListPage (DataTable)", () => {
  it("渲染所有 workspace 行 + 列（name/status/type/vulns/cost/time/操作）", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    expect(screen.getByText("ws-failed")).toBeInTheDocument();
    expect(screen.getByText("ws-corr")).toBeInTheDocument();
    expect(screen.getByText(/\$2\.34/)).toBeInTheDocument();
    expect(screen.getByText("14")).toBeInTheDocument();
  });

  it("搜索框过滤 name", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    const search = screen.getByPlaceholderText(/搜索/i);
    fireEvent.change(search, { target: { value: "failed" } });
    expect(screen.queryByText("ws-a")).not.toBeInTheDocument();
    expect(screen.getByText("ws-failed")).toBeInTheDocument();
  });

  it("status 筛选", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    // Radix Select 不响应 fireEvent.change：开 trigger → 点 option
    fireEvent.click(screen.getByLabelText(/状态筛选/));
    await waitFor(async () => fireEvent.click(await screen.findByRole("option", { name: /失败/ })));
    expect(screen.queryByText("ws-a")).not.toBeInTheDocument();
    expect(screen.getByText("ws-failed")).toBeInTheDocument();
  });

  it("correlation 行 expandable → 展开显子 ws", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-corr")).toBeInTheDocument());
    // 展开按钮（correlation 行）
    const expandBtn = screen.getAllByRole("button", { name: /展开/i })[0];
    fireEvent.click(expandBtn);
    // 子 ws 渲染为 "└─ ws-child1"，用正则匹配
    expect(await screen.findByText(/ws-child1/)).toBeInTheDocument();
  });

  it("running 行有'取消'按钮；点击 → Dialog 确认 → cancelScan", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /取消/ }));
    // Dialog 标题 + 描述都含 "取消扫描"，用 heading role 精确匹配标题
    expect(await screen.findByRole("heading", { name: /取消扫描/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /确认/ }));
    // cancelScan DELETE /api/scan/ws-a 已 mock → 触发 refresh
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("failed 行有'删除'按钮；点击 → Dialog 确认 → deleteWorkspace", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-failed")).toBeInTheDocument());
    // 精确定位 ws-failed 行的删除按钮(Delete 始终可见,running 行也有删除)
    const row = screen.getByText("ws-failed").closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: /删除/ }));
    expect(await screen.findByRole("heading", { name: /删除工作区/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /确认/ }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("running 行同时有'取消'和'删除'按钮(Delete 始终可见,spec §4.7)", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    const row = screen.getByText("ws-a").closest("tr")!;
    expect(within(row).getByRole("button", { name: /取消/ })).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: /删除/ })).toBeInTheDocument();
  });

  it("cancel 返 via:signal → toast 语义提示「已发停止信号」", async () => {
    server.use(http.delete("/api/scan/:ws", () => HttpResponse.json({ cancelled: "ws-a", via: "signal" })));
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /取消/ }));
    fireEvent.click(await screen.findByRole("button", { name: /确认/ }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(/停止信号/)).toBeInTheDocument());
  });

  it("delete API 失败 → toast 错误 + 不卡弹窗(spec §4.7)", async () => {
    server.use(http.delete("/api/workspaces/:ws", () => new HttpResponse(null, { status: 500 })));
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-failed")).toBeInTheDocument());
    const row = screen.getByText("ws-failed").closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: /删除/ }));
    fireEvent.click(await screen.findByRole("button", { name: /确认/ }));
    await waitFor(() => expect(screen.getByText(/操作失败/)).toBeInTheDocument());
  });

  it("空列表 → Empty 空态", async () => {
    server.use(http.get("/api/workspaces", () => HttpResponse.json([])));
    renderPage();
    expect(await screen.findByText(/暂无工作区/)).toBeInTheDocument();
  });

  it("loading → Skeleton 行；上次刷新时间显示", async () => {
    renderPage();
    // lastUpdated 显示（waitFor data 后）
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    expect(screen.getByText(/上次刷新|last updated/i)).toBeInTheDocument();
  });

  it("行首无 status-bar 遗留 class、running 行有 bg-cyan 色条", async () => {
    const { container } = renderPage();
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    expect(container.querySelector(".status-bar")).toBeNull();
    expect(container.querySelector('[class*="bg-cyan"]')).not.toBeNull();
  });

  it("操作列表头与按钮组居中对齐（表头恒在按钮组中心正上方，删除/取消+删除皆然）", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    // 表头「操作」居中（header 渲染为 .text-center 容器）
    expect(screen.getByText("操作").className).toMatch(/text-center/);
    // 按钮组容器 justify-center → 按钮组在单元格内居中
    const delBtn = screen.getAllByRole("button", { name: /删除/ })[0];
    expect(delBtn.closest("div")?.className).toMatch(/justify-center/);
  });

  it("可排序列表头显手型、不可排序的「操作」列表头不显（避免点击无反应的误导）", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    // name 列可排序 → cursor-pointer
    const nameTh = screen.getByRole("columnheader", { name: "工作区" });
    expect(nameTh.className).toMatch(/cursor-pointer/);
    // actions 列 display 不可排序 → 无 cursor-pointer
    const actionsTh = screen.getByText("操作").closest("th");
    expect(actionsTh?.className).not.toMatch(/cursor-pointer/);
  });

  it("workspace 名超长时截断 + tooltip 看全名（防撑宽列表，对齐 repos 名称列）", async () => {
    server.use(
      http.get("/api/workspaces", () => HttpResponse.json([
        { name: "very-long-hostname_20260714-120000-extra", scan_type: "whitebox", status: "completed", created_at: 1780000000, is_correlation: false },
      ])),
    );
    renderPage();
    const link = await screen.findByRole("link", { name: /very-long-hostname/ });
    expect(link.className).toMatch(/truncate/);
  });

  it("精修：渲染页面标题（h1）+ 副标题", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    expect(screen.getByRole("heading", { level: 1, name: "工作区" })).toBeInTheDocument();
    expect(screen.getByText(/所有扫描任务/)).toBeInTheDocument();
  });

  it("精修：概览条聚合 运行中/已完成/失败 计数 + 总成本", async () => {
    const { container } = renderPage();
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    const statValue = (label: string) =>
      Array.from(container.querySelectorAll(".uppercase"))
        .find((n) => n.textContent === label)?.nextElementSibling?.textContent ?? "";
    // ws-a running / ws-corr completed / ws-failed failed；成本 2.34+0.5=2.84
    expect(statValue("运行中")).toBe("1");
    expect(statValue("已完成")).toBe("1");
    expect(statValue("失败")).toBe("1");
    expect(statValue("总成本")).toMatch(/2\.84/);
  });
});

describe("WorkspaceListPage i18n", () => {
  afterEach(() => i18n.changeLanguage("zh"));

  it("zh 渲染中文表头 + 中文搜索框", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    // 表头（中文）—— 用 columnheader role 定位，避免与页面标题 h1「工作区」撞文本
    expect(screen.getByRole("columnheader", { name: "工作区" })).toBeInTheDocument();
    expect(screen.getByText("漏洞数")).toBeInTheDocument();
    expect(screen.getByText("操作")).toBeInTheDocument();
    // 搜索框 placeholder 中文
    expect(screen.getByPlaceholderText(/搜索工作区/)).toBeInTheDocument();
    // 新建扫描按钮中文
    expect(screen.getByRole("button", { name: /新建扫描/ })).toBeInTheDocument();
  });

  it("切英文后表头变英文 Workspace/Vulns/Actions", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-a")).toBeInTheDocument());
    await act(async () => {
      await i18n.changeLanguage("en");
    });
    expect(screen.getByText("Workspace", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("Vulns")).toBeInTheDocument();
    expect(screen.getByText("Actions")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/search workspaces/i)).toBeInTheDocument();
  });
});
