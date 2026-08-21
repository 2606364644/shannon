import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { renderWithSwr } from "@/test/swr-render";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import { ScanList } from "./ScanList";

// toast 在 ScanList 用于操作反馈；隔离避免 sonner 全局副作用。
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

// 捕获 useNavigate 调用以断言重跑的 location.state 预填（MemoryRouter 仍用 actual）。
const { navMock } = vi.hoisted(() => ({ navMock: vi.fn() }));
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navMock };
});

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
// cancelled（2026-08-17 根因修）：取消手动黑盒 run 后任务级落 cancelled——终态口径
// （后端 resume 拒 422），显 查看/重跑/删除，不显恢复/取消。
const cancelled = {
  scan_id: "s4", scan_type: "whitebox", status: "cancelled", created_at: 4000,
  completed_at: 5000, vuln_count: 0, total_cost_usd: 1, cost_currency: "USD", is_running: false,
} as const;

let listCalls = 0;
const server = setupServer(
  http.get("/api/workspaces/:ws/scans", () => { listCalls++; return HttpResponse.json([]); }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => { i18n.changeLanguage("zh"); listCalls = 0; navMock.mockClear(); });
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderList() {
  return renderWithSwr(
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
    // v4：列表头 CTA 隐藏，空态卡是唯一「新建扫描」link
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

  it("仓库格显示 repo@branch（分支快照），commit 前 8 位进 title；无快照行不显示 @", async () => {
    // spec 2026-08-21 §4：切分支后同一仓扫不同分支，报告靠快照区分来源。
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([
        { ...completed, scan_id: "with-snap", repo: "app", repo_url: "https://x/app.git",
          repo_branch: "dev", repo_commit: "abc123def456" },
        { ...completed, scan_id: "no-snap", repo: "old", repo_branch: null, repo_commit: null },
      ])),
    );
    renderList();
    // repo 与 @branch 分属两个 span（样式分档），默认 text matcher 只看单文本节点 →
    // 用 textContent 函数匹配
    const repoCell = await waitFor(() => {
      const hits = screen.getAllByText((_, el) => el?.textContent === "app@dev");
      expect(hits.length).toBeGreaterThan(0);
      const td = hits[0].closest("td");
      expect(td).not.toBeNull();
      return td as HTMLElement;
    });
    // commit 前 8 位 hover 可见（title 属性）
    expect(repoCell).toHaveAttribute("title", "abc123de");
    // 存量报告（无快照）不显示 @ 分支
    expect(screen.getByText("old")).toBeInTheDocument();
    expect(screen.queryByText((_, el) => (el?.textContent ?? "").includes("old@"))).not.toBeInTheDocument();
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

  it("cancelled scan：不显恢复/取消，显删除/重跑/查看（终态口径）", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([cancelled])));
    renderList();
    await waitFor(() => expect(screen.getByText("s4")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "恢复" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "取消" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重跑/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /删除/ })).toBeInTheDocument();
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
  it("取消 running scan -> POST cancel scan-scoped -> 刷新列表", async () => {
    const cancelCalls: string[] = [];
    server.use(
      http.get("/api/workspaces/:ws/scans", () => { listCalls++; return HttpResponse.json([running]); }),
      http.post("/api/workspaces/:ws/scans/:scanId/cancel", ({ params }) => {
        cancelCalls.push(`${params.ws}/${params.scanId}`);
        return HttpResponse.json({ cancelled: params.scanId as string });
      }),
    );
    renderList();
    await waitFor(() => expect(screen.getByText("s1")).toBeInTheDocument());
    const listCallsBefore = listCalls;
    // 点卡片「取消」-> Dialog 开
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(await screen.findByText(/取消扫描 s1/)).toBeInTheDocument();
    // 点「确认」-> POST cancel
    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    await waitFor(() => expect(cancelCalls).toEqual(["ws/s1"]));
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

  it("重跑（黑盒）-> getScan 拿配置 -> 跳 /scan/new 并预填 location state", async () => {
    const auth = {
      login_type: "form", login_url: "http://t.example/login",
      credentials: { username: "admin" },
    };
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([completed])),
      http.get("/api/workspaces/:ws/scans/:scanId", () =>
        HttpResponse.json({ scan_type: "blackbox", web_url: "http://t.example",
          reuse_whitebox_scan_id: "wb-1", authentication: auth,
          host_profile_id: "host-profile-1", host_url: null,
          host_source: "profile", host_mapping_count: 2 })),
    );
    renderList();
    await waitFor(() => expect(screen.getByText("s2")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /重跑/ }));
    await waitFor(() => expect(navMock).toHaveBeenCalled());
    expect(navMock).toHaveBeenCalledWith("/scan/new?workspace=ws", {
      state: { type: "blackbox", workspace: "ws", url: "http://t.example",
        reuseScanId: "wb-1", auth, hostProfileId: "host-profile-1" },
    });
  });

  it("重跑（白盒）-> 预填 source_repo", async () => {
    server.use(
      // 重跑仅终态行有（interrupted 走「恢复」）——用已完成的白盒扫描做 fixture。
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([
        { scan_id: "s3", scan_type: "whitebox", status: "completed", created_at: 3000,
          completed_at: 4000, vuln_count: 1, total_cost_usd: 0.5, cost_currency: "USD",
          is_running: false, workflow_id: "ws-s3" }])),
      http.get("/api/workspaces/:ws/scans/:scanId", () =>
        HttpResponse.json({ scan_type: "whitebox", source_repo: "group/repo-a" })),
    );
    renderList();
    await waitFor(() => expect(screen.getByText("ws-s3")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /重跑/ }));
    await waitFor(() => expect(navMock).toHaveBeenCalled());
    expect(navMock).toHaveBeenCalledWith("/scan/new?workspace=ws", {
      state: { type: "whitebox", workspace: "ws", repo: "group/repo-a" },
    });
  });

  it("重跑 getScan 失败 -> 降级跳转（无 state）+ toast 提示", async () => {
    const { toast } = await import("sonner");
    server.use(
      http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([completed])),
      http.get("/api/workspaces/:ws/scans/:scanId", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    renderList();
    await waitFor(() => expect(screen.getByText("s2")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /重跑/ }));
    await waitFor(() => expect(navMock).toHaveBeenCalledWith("/scan/new?workspace=ws"));
    // 降级：仅 path 参数，无 state。
    expect(navMock.mock.calls.some(([p, o]) => p === "/scan/new?workspace=ws" && !o)).toBe(true);
    expect(toast.error).toHaveBeenCalled();
  });
});

describe("ScanList 表格设计不变量（重设计 2026-08-15：漏洞数 hero 呈现）", () => {
  it("vuln_count > 0：漏洞数以 mono + 红色呈现（醒目）", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([completed])));
    renderList();
    await waitFor(() => expect(screen.getByText("s2")).toBeInTheDocument());
    // 漏洞单元格：mono 大号 + text-red
    const v = screen.getByTestId("row-vulns-s2");
    expect(v.textContent).toBe("3");
    expect(v.className).toMatch(/font-mono/);
    expect(v.className).toMatch(/text-red/);
  });

  it("vuln_count = 0：漏洞值中性色（不染红，避免空扫描虚警）", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([running])));
    renderList();
    await waitFor(() => expect(screen.getByText("s1")).toBeInTheDocument());
    const v = screen.getByTestId("row-vulns-s1");
    expect(v.textContent).toBe("0");
    expect(v.className).toMatch(/font-mono/);
    expect(v.className).not.toMatch(/text-red/);
    // v4：0 进一步弱化为 muted（低于正文亮度，视觉降噪）
    expect(v.className).toMatch(/text-muted-foreground/);
  });

  it("花费以 mono 呈现", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([completed])));
    renderList();
    await waitFor(() => expect(screen.getByText("s2")).toBeInTheDocument());
    expect(screen.getByText("$2.00")).toBeInTheDocument();
  });

  it("状态分段过滤：点「已完成」只剩 completed 行", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([running, completed])));
    renderList();
    await waitFor(() => expect(screen.getByText("s1")).toBeInTheDocument());
    // 分段计数：全部按钮 textContent = 「全部2」（直接文本 + 计数 span）
    const allBtn = screen.getByText("全部", { selector: "button" });
    expect(allBtn.textContent).toBe("全部2");
    fireEvent.click(screen.getByText("已完成", { selector: "button" }));
    expect(screen.queryByText("s1")).not.toBeInTheDocument();
    expect(screen.getByText("s2")).toBeInTheDocument();
  });
});

// v4（workspace-page-preview-v4.html）：整行可点 + 空工作区收敛。
describe("ScanList v4：整行可点 + 空态收敛", () => {
  it("点击行非交互区 -> 导航到默认 tab（completed -> report）", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([completed])));
    renderList();
    await waitFor(() => expect(screen.getByText("s2")).toBeInTheDocument());
    // 点花费单元格（非交互且文案唯一——「已完成」会同时命中状态徽标与过滤分段按钮）
    fireEvent.click(screen.getByText("$2.00"));
    await waitFor(() => expect(navMock).toHaveBeenCalledWith("/p/ws/scans/s2/report"));
  });

  it("操作按钮点击不触发行导航（stopPropagation）", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([completed])));
    renderList();
    await waitFor(() => expect(screen.getByText("s2")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /删除/ }));
    expect(await screen.findByText(/删除扫描 s2/)).toBeInTheDocument();
    expect(navMock).not.toHaveBeenCalled();
  });

  it("空态：过滤器 + 列表头 CTA 隐藏；空态卡唯一 CTA + 仓库/认证次级入口", async () => {
    server.use(http.get("/api/workspaces/:ws/scans", () => HttpResponse.json([])));
    renderList();
    await waitFor(() => expect(screen.getByText(/尚无扫描任务/)).toBeInTheDocument());
    // 过滤器整体隐藏（无对象可过滤）
    expect(screen.queryByText("全部", { selector: "button" })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText("搜索 scan / workflow / repo…")).not.toBeInTheDocument();
    // CTA 唯一化：列表头 CTA 移除，仅空态卡一个「新建扫描」
    expect(screen.getAllByRole("link", { name: /新建扫描/ })).toHaveLength(1);
    // 次级入口：先配置仓库 / 配置认证档案（对应命令栏入口）
    expect(screen.getByRole("link", { name: /先配置仓库/ })).toHaveAttribute("href", "/p/ws/repos");
    expect(screen.getByRole("link", { name: /配置认证档案/ })).toHaveAttribute("href", "/p/ws/auth-profiles");
  });
});
