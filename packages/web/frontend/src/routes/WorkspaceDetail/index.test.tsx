import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import WorkspaceDetail from "./index";

// MemberManagerDialog 依赖 AuthProvider + 自有成员 API；ws 概览测试聚焦 header/入口/404，
// 隔离该子组件（其行为在 MemberManagerDialog.test.tsx 独立覆盖）。
vi.mock("@/components/MemberManagerDialog", () => ({
  MemberManagerDialog: () => null,
}));

// Task 9：WorkspaceDetail header 新增置顶按钮（useAuth）+ WorkspaceSwitcher 入口
// （useAuth + useWorkspaces）。ws 概览测试聚焦 header/入口/404，隔离这些 hook 的真实
// 网络与 provider 依赖（其行为在 WorkspaceSwitcher.test.tsx 独立覆盖）。
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: { id: 1, username: "admin", role: "admin", must_change_password: false, pinned_workspace: null },
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
  }),
}));

vi.mock("@/api/useWorkspaces", () => ({
  useWorkspaces: () => ({
    data: [],
    loading: false,
    lastUpdated: new Date(),
    error: null,
    refresh: vi.fn(),
  }),
}));

const server = setupServer(
  // ws 概览 header fetch GET /workspaces/{ws}（shim 返 latest scan payload + scans[]）。
  http.get("/api/workspaces/:ws", () =>
    HttpResponse.json({
      status: "running",
      scan_type: "whitebox",
      scans: [{ scan_id: "s1" }, { scan_id: "s2" }],
    }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => {
  server.resetHandlers();
  cleanup();
  i18n.changeLanguage("zh");
});
afterAll(() => server.close());

// index/repos/settings 用占位 div 替换，聚焦 WorkspaceDetail 布局本身（header + Outlet），
// 不引入 ScanList/ReposTab 的自有请求。
function renderAt(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/p/:workspace" element={<WorkspaceDetail />}>
          <Route index element={<div>scanlist-content</div>} />
          <Route path="repos" element={<div>repos-content</div>} />
          <Route path="settings" element={<div>settings-content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("WorkspaceDetail ws 概览", () => {
  it("渲染 ws 名 + 返回列表链接 + 仓库/settings 入口", async () => {
    renderAt("/p/ws");
    expect(screen.getByText("ws")).toBeInTheDocument();
    expect(screen.getByText(/返回列表/)).toBeInTheDocument();
    // 仓库入口链接（含「仓库」文案）
    expect(screen.getByRole("link", { name: /仓库/ })).toBeInTheDocument();
    // settings 入口（齿轮，aria-label 来自 wsConfig.openSettings）
    expect(screen.getByRole("link", { name: /配置|settings/i })).toBeInTheDocument();
    // index Outlet 渲染扫描列表占位
    expect(screen.getByText("scanlist-content")).toBeInTheDocument();
  });

  it("header 显 latest_status badge + scan_count 聚合（scans[].length）", async () => {
    renderAt("/p/ws");
    // scans 数组长度 2 -> 「扫描任务 · 2」
    await waitFor(() => expect(screen.getByText(/扫描任务 · 2/)).toBeInTheDocument());
  });

  it("点击仓库入口 -> 导航到 repos", async () => {
    renderAt("/p/ws");
    const { fireEvent } = await import("@testing-library/react");
    fireEvent.click(screen.getByRole("link", { name: /仓库/ }));
    await waitFor(() => expect(screen.getByText("repos-content")).toBeInTheDocument());
  });

  it("返回列表链接渲染（中文）", () => {
    renderAt("/p/ws");
    expect(screen.getByText(/返回列表/)).toBeInTheDocument();
  });
});

describe("WorkspaceDetail ws 概览 i18n", () => {
  it("切英文 -> 返回列表 + 仓库入口为英文", async () => {
    i18n.changeLanguage("en");
    renderAt("/p/ws");
    expect(screen.getByText(/Back to list/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Repositories/ })).toBeInTheDocument();
  });
});

describe("WorkspaceDetail ws 概览 notFound", () => {
  it("404 -> 显 notFound 消息（中文）", async () => {
    server.use(http.get("/api/workspaces/:ws", () => HttpResponse.json({ detail: "nope" }, { status: 404 })));
    renderAt("/p/ghost");
    await waitFor(() => expect(screen.getByText(/工作区不存在或已被删除/)).toBeInTheDocument());
    expect(screen.getByText(/返回列表/)).toBeInTheDocument();
  });

  it("404 + 切英文 -> notFound 消息英文", async () => {
    i18n.changeLanguage("en");
    server.use(http.get("/api/workspaces/:ws", () => HttpResponse.json({ detail: "nope" }, { status: 404 })));
    renderAt("/p/ghost");
    await waitFor(() => expect(screen.getByText(/does not exist or has been deleted/i)).toBeInTheDocument());
    expect(screen.getByText(/Back to list/)).toBeInTheDocument();
  });
});
