import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { render, screen, waitFor, cleanup, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import { DashboardPage } from "./DashboardPage";
import type { Workspace } from "../api/types";

// 用「今天」的 unix 秒,验证 isToday 过滤
const todaySec = Math.floor(Date.now() / 1000);
const oldSec = todaySec - 3 * 86400; // 3 天前

const workspaces: Workspace[] = [
  { name: "ws-run", scan_type: "whitebox", status: "running", created_at: todaySec, total_cost_usd: 1.5, total_duration_ms: 120000, vuln_count: 3, is_correlation: false },
  { name: "ws-today", scan_type: "blackbox", status: "completed", created_at: oldSec, completed_at: todaySec, total_cost_usd: 2.0, total_duration_ms: 50000, vuln_count: 5, is_correlation: false },
  { name: "ws-old", scan_type: "whitebox", status: "completed", created_at: oldSec, completed_at: oldSec, total_cost_usd: 0.5, total_duration_ms: 30000, vuln_count: 7, is_correlation: false },
];

const server = setupServer(
  http.get("/api/workspaces", () => HttpResponse.json(workspaces)),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
// jsdom navigator.language 默认 en，LanguageDetector 会把 i18n 切到 en；现有断言依赖中文渲染，逐测试钉回 zh。
beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderPage() {
  return render(<MemoryRouter><DashboardPage /></MemoryRouter>);
}

describe("DashboardPage 骨架 + 汇总", () => {
  it("全空态(data=[])→ 引导卡 + 新建扫描按钮", async () => {
    server.use(http.get("/api/workspaces", () => HttpResponse.json([])));
    renderPage();
    expect(await screen.findByText(/还没有扫描/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /\+ 新建扫描/ })).toHaveAttribute("href", "/scan/new");
  });

  it("汇总数字:运行中 1 / 今日完成 1 / 累计漏洞 15 / 累计 cost 4.00", async () => {
    renderPage();
    // 等累计漏洞出现(Task4 只渲染汇总行;ws-run 名字是 Task5 才渲染)
    await waitFor(() => expect(screen.getByText("15")).toBeInTheDocument());
    // 4 个汇总值;用 getAllByText 精确匹配数字
    // 运行中=1, 今日完成=1 → 两个 "1"(精确匹配,不撞 $1.50 等含 1 的文本)
    expect(screen.getAllByText("1")).toHaveLength(2);
    // 累计漏洞 = 3+5+7 = 15
    expect(screen.getByText("15")).toBeInTheDocument();
    // 累计 cost = 1.5+2.0+0.5 = 4.00
    expect(screen.getByText("$4.00")).toBeInTheDocument();
  });

  it("顶栏「+ 新建扫描」入口跳 /scan/new", async () => {
    renderPage();
    // 等累计漏洞出现(Task4 只渲染汇总行;ws-run 名字是 Task5 才渲染)
    await waitFor(() => expect(screen.getByText("15")).toBeInTheDocument());
    const links = screen.getAllByRole("link", { name: /\+ 新建扫描/ });
    expect(links.some((l) => l.getAttribute("href") === "/scan/new")).toBe(true);
  });

  it("error → ErrorState(role=alert)+ 重试", async () => {
    server.use(http.get("/api/workspaces", () => HttpResponse.json({}, { status: 500 })));
    renderPage();
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /重试/ })).toBeInTheDocument();
  });

  it("loading → Skeleton", async () => {
    server.use(http.get("/api/workspaces", () => new Promise(() => {}))); // 永不 resolve
    renderPage();
    expect(document.querySelector(".animate-pulse")).toBeInTheDocument();
  });

  it("running 卡片墙:整张可点跳 /p/ws-run/live", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-run")).toBeInTheDocument());
    const link = screen.getByRole("link", { name: /查看实时/ });
    expect(link.getAttribute("href")).toBe("/p/ws-run/live");
  });

  it("最近扫描区:非 running 行 + 「查看全部」跳 /workspaces", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-today")).toBeInTheDocument());
    expect(screen.getByText("ws-old")).toBeInTheDocument();
    // 「查看全部 →」跳列表页
    expect(screen.getByRole("link", { name: /查看全部/ }).getAttribute("href")).toBe("/workspaces");
    // 最近行整行可点跳 /p/{ws}
    expect(screen.getByRole("link", { name: /ws-today/ }).getAttribute("href")).toBe("/p/ws-today");
  });

  it("无 running → 显示空态文案", async () => {
    server.use(http.get("/api/workspaces", () => HttpResponse.json([
      { name: "ws-done", scan_type: "whitebox", status: "completed", created_at: todaySec, completed_at: todaySec, vuln_count: 1, is_correlation: false },
    ])));
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-done")).toBeInTheDocument());
    expect(screen.getByText(/当前无运行中扫描/)).toBeInTheDocument();
  });

  it("标题区统一为 PageHeader：显「仪表盘」+ 副标题", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "仪表盘" })).toBeInTheDocument();
    expect(screen.getByText("扫描概览与最近活动")).toBeInTheDocument();
  });
});

describe("DashboardPage i18n", () => {
  afterEach(() => i18n.changeLanguage("zh"));

  it("中文渲染汇总标签与最近扫描区标题", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-run")).toBeInTheDocument());
    expect(screen.getByText("今日完成")).toBeInTheDocument();
    expect(screen.getByText("累计漏洞")).toBeInTheDocument();
    expect(screen.getByText("最近扫描")).toBeInTheDocument();
  });

  it("切英文后汇总标签变 Completed today 等", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-run")).toBeInTheDocument());
    await act(async () => {
      await i18n.changeLanguage("en");
    });
    expect(await screen.findByText("Completed today")).toBeInTheDocument();
    expect(screen.getByText("Total vulns")).toBeInTheDocument();
    expect(screen.getByText("Recent scans")).toBeInTheDocument();
  });
});
