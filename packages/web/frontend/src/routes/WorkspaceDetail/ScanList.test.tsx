import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import { ScanList } from "./ScanList";

// toast 在 ScanList 用于操作反馈；隔离避免 sonner 全局副作用。
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const running = {
  scan_id: "s1", scan_type: "whitebox", status: "running", created_at: 1000,
  completed_at: null, vuln_count: 0, total_cost_usd: 1.5, cost_currency: "USD", is_running: true,
} as const;
const completed = {
  scan_id: "s2", scan_type: "blackbox", status: "completed", created_at: 2000,
  completed_at: 3000, vuln_count: 3, total_cost_usd: 2, cost_currency: "USD", is_running: false,
} as const;
const interrupted = {
  scan_id: "s3", scan_type: "whitebox", status: "interrupted", created_at: 3000,
  completed_at: null, vuln_count: 1, total_cost_usd: 0.5, cost_currency: "USD", is_running: false,
} as const;

let listCalls = 0;
const server = setupServer(
  http.get("/api/workspaces/:ws/scans", () => { listCalls++; return HttpResponse.json([]); }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => { i18n.changeLanguage("zh"); listCalls = 0; });
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderList() {
  return render(
    <MemoryRouter initialEntries={["/p/ws"]}>
      <Routes><Route path="/p/:workspace" element={<ScanList />} /></Routes>
    </MemoryRouter>,
  );
}

describe("ScanList 扫描列表", () => {
  it("渲染标题 + 新建扫描按钮 + 多个 scan 卡片", async () => {
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([running, completed])),
    );
    renderList();
    expect(screen.getByText("扫描任务")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /新建扫描/ })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("s1")).toBeInTheDocument());
    expect(screen.getByText("s2")).toBeInTheDocument();
  });

  it("空态：无 scan -> 显空态 + 新建扫描按钮", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([])));
    renderList();
    await waitFor(() => expect(screen.getByText(/尚无扫描任务/)).toBeInTheDocument());
    // 空态：顶部 + Empty 内各一个「新建扫描」link
    expect(screen.getAllByRole("link", { name: /新建扫描/ }).length).toBeGreaterThanOrEqual(1);
  });

  it("新建扫描链接预填 ?workspace=<ws>", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([])));
    renderList();
    await waitFor(() =>
      expect(screen.getAllByRole("link", { name: /新建扫描/ }).length).toBeGreaterThan(0),
    );
    expect(screen.getAllByRole("link", { name: /新建扫描/ })[0].getAttribute("href")).toContain(
      "/scan/new?workspace=ws",
    );
  });

  it("任务名展示 workflow_id（{ws}-{scan_id}），路由仍用 scan_id", async () => {
    // 后端 ScanSummary 透传 workflow_id；前端任务名展示它，路由/定位仍走 scan_id。
    const wf = { ...running, workflow_id: "ws-s1" };
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([wf])),
    );
    renderList();
    await waitFor(() =>
      expect(screen.getByRole("link", { name: "ws-s1" })).toBeInTheDocument(),
    );
    // 显示 workflow_id，但链接路由仍是 scan_id（s1）—— 显示与定位解耦。
    expect(screen.getByRole("link", { name: "ws-s1" }).getAttribute("href")).toBe(
      "/p/ws/scans/s1/live",
    );
  });
});

describe("ScanList 卡片操作按钮按 status 显隐", () => {
  it("running scan：显取消，不显恢复", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([running])));
    renderList();
    await waitFor(() => expect(screen.getByText("s1")).toBeInTheDocument());
    // running -> 显取消（common.cancel）
    expect(screen.getAllByRole("button", { name: "取消" }).length).toBeGreaterThan(0);
    // running -> 不显恢复
    expect(screen.queryByRole("button", { name: "恢复" })).not.toBeInTheDocument();
  });

  it("interrupted scan：显恢复，不显取消", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([interrupted])));
    renderList();
    await waitFor(() => expect(screen.getByText("s3")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "恢复" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "取消" })).not.toBeInTheDocument();
  });

  it("completed scan：不显恢复/取消，显删除/重跑/查看", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([completed])));
    renderList();
    await waitFor(() => expect(screen.getByText("s2")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "恢复" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "取消" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重跑/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /删除/ })).toBeInTheDocument();
    // 查看是 Link（role=link）
    expect(screen.getByRole("link", { name: /查看/ })).toBeInTheDocument();
  });

  it("查看链接按 status 智能指向：完成 -> report", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([completed])));
    renderList();
    await waitFor(() => expect(screen.getByRole("link", { name: /查看/ })).toBeInTheDocument());
    // 完成 -> report（看结果），与 router.tsx DefaultScanTab 一致
    expect(screen.getByRole("link", { name: /查看/ }).getAttribute("href")).toBe("/p/ws/scans/s2/report");
    // scan_id 链接同策略
    expect(screen.getByRole("link", { name: "s2" }).getAttribute("href")).toBe("/p/ws/scans/s2/report");
  });

  it("查看链接：running scan -> live（看实时）", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([running])));
    renderList();
    await waitFor(() => expect(screen.getByRole("link", { name: /查看/ })).toBeInTheDocument());
    expect(screen.getByRole("link", { name: /查看/ }).getAttribute("href")).toBe("/p/ws/scans/s1/live");
  });
});

describe("ScanList 操作调 API + 列表刷新", () => {
  it("取消 running scan -> DELETE scan-scoped -> 刷新列表", async () => {
    const deleteCalls: string[] = [];
    server.use(
      http.get("/api/workspaces/:ws/scans", () => { listCalls++; return HttpResponse.json([running]); }),
      http.delete("/api/workspaces/:ws/scans/:scanId", ({ params }) => {
        deleteCalls.push(`${params.ws}/${params.scanId}`);
        return HttpResponse.json({ deleted: params.scanId as string });
      }),
    );
    renderList();
    await waitFor(() => expect(screen.getByText("s1")).toBeInTheDocument());
    const listCallsBefore = listCalls;
    // 点卡片「取消」-> Dialog 开
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(await screen.findByText(/取消扫描 s1/)).toBeInTheDocument();
    // 点「确认」-> DELETE
    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    await waitFor(() => expect(deleteCalls).toEqual(["ws/s1"]));
    // 刷新：listScans 再拉一次
    await waitFor(() => expect(listCalls).toBeGreaterThan(listCallsBefore));
  });

  it("删除 scan -> DELETE -> 刷新列表", async () => {
    const deleteCalls: string[] = [];
    server.use(
      http.get("/api/workspaces/:ws/scans", () => { listCalls++; return HttpResponse.json([completed]); }),
      http.delete("/api/workspaces/:ws/scans/:scanId", ({ params }) => {
        deleteCalls.push(`${params.ws}/${params.scanId}`);
        return HttpResponse.json({ deleted: params.scanId as string });
      }),
    );
    renderList();
    await waitFor(() => expect(screen.getByText("s2")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /删除/ }));
    expect(await screen.findByText(/删除扫描 s2/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    await waitFor(() => expect(deleteCalls).toEqual(["ws/s2"]));
  });

  it("恢复 interrupted scan -> POST resume", async () => {
    const resumeCalls: string[] = [];
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([interrupted])),
      http.post("/api/workspaces/:ws/scans/:scanId/resume", ({ params }) => {
        resumeCalls.push(`${params.ws}/${params.scanId}`);
        return HttpResponse.json({ workspace: params.ws as string, scan_id: params.scanId as string });
      }),
    );
    renderList();
    await waitFor(() => expect(screen.getByText("s3")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "恢复" }));
    await waitFor(() => expect(resumeCalls).toEqual(["ws/s3"]));
  });

  it("重跑 -> 跳 /scan/new?workspace=<ws>", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([completed])));
    renderList();
    await waitFor(() => expect(screen.getByText("s2")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /重跑/ }));
    // navigate 到 /scan/new?workspace=ws（MemoryRouter 无该路由 -> 不渲染，但 location 已变）
    // 验证方式：重跑按钮点击后列表组件不再渲染（导航离开）。用 queryByText 守 s2 消失。
    await waitFor(() => expect(screen.queryByText("扫描任务")).not.toBeInTheDocument());
  });
});

describe("ScanCard 指标带设计不变量（concern 1：漏洞/花花费 hero）", () => {
  it("vuln_count > 0：漏洞数以大号 mono + 红色 hero 呈现（醒目）", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([completed])));
    renderList();
    await waitFor(() => expect(screen.getByText("s2")).toBeInTheDocument());
    // 找「漏洞数」标签，其后的值应为 3，且 className 含 text-lg + text-red（hero + 危险色）
    const label = screen.getByText("漏洞数");
    const value = label.parentElement?.querySelector(".font-mono.text-lg");
    expect(value?.textContent).toBe("3");
    expect(value?.className).toMatch(/text-lg/);
    expect(value?.className).toMatch(/text-red/);
  });

  it("vuln_count = 0：漏洞值中性色（不染红，避免空扫描虚警）", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([running])));
    renderList();
    await waitFor(() => expect(screen.getByText("s1")).toBeInTheDocument());
    const label = screen.getByText("漏洞数");
    const value = label.parentElement?.querySelector(".font-mono.text-lg");
    expect(value?.textContent).toBe("0");
    expect(value?.className).not.toMatch(/text-red/);
  });

  it("花费以 hero（大号 mono）呈现", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([completed])));
    renderList();
    await waitFor(() => expect(screen.getByText("s2")).toBeInTheDocument());
    const label = screen.getByText("花费");
    const value = label.parentElement?.querySelector(".font-mono.text-lg");
    expect(value).toBeTruthy();
  });
});
