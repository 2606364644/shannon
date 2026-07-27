import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import WorkspaceDetail from "./index";

// MemberManagerDialog 依赖 AuthProvider + 自有成员 API；header 测试聚焦元信息/降级/404，
// 隔离该子组件（其行为在 MemberManagerDialog.test.tsx 独立覆盖）。
vi.mock("@/components/MemberManagerDialog", () => ({
  MemberManagerDialog: () => null,
}));
// WorkspaceSwitcher（Task 9 入口）依赖 useAuth + useWorkspaces（GET /api/workspaces）；
// header 测试不断言切换器，隔离避免 AuthProvider + workspaces API 噪音（行为在
// WorkspaceSwitcher.test.tsx 独立覆盖）。
vi.mock("@/components/WorkspaceSwitcher", () => ({
  WorkspaceSwitcher: () => null,
}));
// WorkspaceDetail header 置顶按钮（Task 9）依赖 useAuth（user.pinned_workspace + refreshUser）；
// header 测试不断言置顶态，隔离 useAuth 避免 AuthProvider + /auth/me 噪音。
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: { id: 1, username: "admin", role: "admin", must_change_password: false },
    refreshUser: vi.fn(),
  }),
}));

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
// jsdom navigator.language 默认 en；现有断言依赖中文渲染，逐测试钉回 zh。
beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => { server.resetHandlers(); cleanup(); i18n.changeLanguage("zh"); });
afterAll(() => server.close());

// ws 概览：/p/:ws index 渲染扫描列表占位（不引入 ScanList 自有 listScans 请求）。
function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/p/:workspace" element={<WorkspaceDetail />}>
          <Route index element={<div>scanlist-content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("WorkspaceDetail header", () => {
  it("显返回链接 + workspace 名 + 元信息（status/scan_type）", async () => {
    server.use(
      http.get("/api/workspaces/:ws", () =>
        HttpResponse.json({ status: "completed", scan_type: "whitebox", scans: [] }),
      ),
    );
    const { container } = renderAt("/p/ws1");
    expect(screen.getByText("返回列表")).toBeInTheDocument();
    expect(screen.getByText("ws1")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("whitebox")).toBeInTheDocument());
    // StatusBadge 兜底显 completed（title 属性保留原 status，不受 i18n 标签本地化影响）
    expect(container.querySelector("[title='completed']")).toBeInTheDocument();
  });

  it("fetch 失败降级显 workspace 名（不阻塞 Outlet）", async () => {
    server.use(http.get("/api/workspaces/:ws", () => new HttpResponse(null, { status: 500 })));
    renderAt("/p/ws1");
    await waitFor(() => expect(screen.getByText("ws1")).toBeInTheDocument());
    expect(screen.getByText("返回列表")).toBeInTheDocument();
    // Outlet 仍渲染（降级不阻塞子路由）
    expect(screen.getByText("scanlist-content")).toBeInTheDocument();
  });

  it("404 显工作区不存在/已删除", async () => {
    server.use(http.get("/api/workspaces/:ws", () =>
      HttpResponse.json({ detail: "workspace not found" }, { status: 404 }),
    ));
    renderAt("/p/ws1");
    await waitFor(() => expect(screen.getByText(/工作区不存在或已被删除/)).toBeInTheDocument());
    expect(screen.getByText("返回列表")).toBeInTheDocument();
  });
});
