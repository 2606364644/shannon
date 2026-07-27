import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { TopBar } from "./TopBar";
import i18n from "@/i18n";

// TopBar 集成 UserMenu（T17），UserMenu 用 useAuth。本测试聚焦 TopBar 业务（导航/i18n/sticky），
// 不测 auth 行为，故 stub useAuth 注入固定 user 避免 "useAuth 必须在 AuthProvider 内使用"。
// 用 vi.hoisted 暴露 mockUseAuth，逐测试切换 user.role 验证 admin 专属「工作区管理」入口。
const { mockUseAuth } = vi.hoisted(() => ({ mockUseAuth: vi.fn() }));
vi.mock("@/auth/AuthContext", () => ({ useAuth: () => mockUseAuth() }));

const userUser = { id: 1, username: "alice", role: "user", must_change_password: false };
const userAdmin = { id: 2, username: "root", role: "admin", must_change_password: false };

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="*" element={<TopBar />} />
      </Routes>
    </MemoryRouter>
  );
}

// 默认普通用户（admin 入口不渲染）；admin 专属 describe 内 beforeEach 覆写。
beforeEach(() => {
  mockUseAuth.mockReturnValue({ user: userUser, loading: false, login: vi.fn(), logout: vi.fn() });
});

describe("TopBar", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("品牌字标 Supernova + 主导航", () => {
    renderAt("/");
    expect(screen.getByText(/Supernova/i)).toBeInTheDocument();
    expect(screen.getByText("工作区")).toBeInTheDocument();
    expect(screen.getByText("扫描")).toBeInTheDocument();
  });

  it("Dashboard / Settings DSF 阶段 disabled（非 <a>）", () => {
    renderAt("/");
    const dash = screen.getByText("概览");
    const settings = screen.getByText("设置");
    expect(dash.tagName).not.toBe("A");
    expect(settings.tagName).not.toBe("A");
    expect(dash.closest("[aria-disabled='true']") ?? dash.parentElement?.closest("[aria-disabled='true']") ?? dash).toBeTruthy();
  });

  it("当前 /scan/new → Scan NavLink data-active=true", () => {
    renderAt("/scan/new");
    expect(screen.getByText("扫描").getAttribute("data-active")).toBe("true");
  });

  it("非当前路由 NavLink data-active=false", () => {
    renderAt("/");
    expect(screen.getByText("扫描").getAttribute("data-active")).toBe("false");
  });

  it("含主题切换入口", () => {
    renderAt("/");
    expect(screen.getByRole("button", { name: /切换主题/ })).toBeInTheDocument();
  });
});

describe("TopBar i18n", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("中文渲染主导航文本（工作区 + 扫描）", () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>
    );
    expect(screen.getByText("工作区")).toBeInTheDocument();
    expect(screen.getByText("扫描")).toBeInTheDocument();
  });

  it("切英文后导航 Workspaces / Scan 文本切换", async () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>
    );
    await act(async () => {
      await i18n.changeLanguage("en");
    });
    expect(screen.getByText("Workspaces")).toBeInTheDocument();
    expect(screen.getByText("Scan")).toBeInTheDocument();
  });

  it("P2: 顶级 /repos nav 已撤销（仓库迁入 ws 内 tab）", () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>
    );
    // 「仓库」/ Repositories 不再作为顶级 nav 项
    expect(screen.queryByText("仓库")).not.toBeInTheDocument();
  });

  it("渲染语言切换器", () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>
    );
    expect(screen.getByLabelText("切换语言")).toBeInTheDocument();
  });
});

describe("TopBar sticky 吸顶", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("header 含 sticky/top-0/z-40/print:static（全局吸顶，低于弹窗 z-50）", () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>
    );
    const header = screen.getByTestId("topbar");
    expect(header.tagName).toBe("HEADER");
    expect(header.className).toContain("sticky");
    expect(header.className).toContain("top-0");
    expect(header.className).toContain("z-40");
    expect(header.className).toContain("print:static");
  });
});

describe("TopBar admin 入口", () => {
  beforeEach(() => {
    i18n.changeLanguage("zh");
    mockUseAuth.mockReturnValue({ user: userAdmin, loading: false, login: vi.fn(), logout: vi.fn() });
  });

  it("admin 可见「工作区管理」入口指向 /workspaces", () => {
    renderAt("/workspaces");
    const entry = screen.getByTestId("nav-workspace-manage");
    expect(entry).toHaveTextContent("工作区管理");
    expect(entry.closest("a")).toHaveAttribute("href", "/workspaces");
  });

  it("普通用户不可见「工作区管理」入口", () => {
    mockUseAuth.mockReturnValue({ user: userUser, loading: false, login: vi.fn(), logout: vi.fn() });
    renderAt("/");
    expect(screen.queryByTestId("nav-workspace-manage")).not.toBeInTheDocument();
  });

  it("顶栏「工作区」入口改跳 /workspaces-entry（IA 重设计 §2.3）", () => {
    renderAt("/workspaces-entry");
    const link = screen.getByText("工作区").closest("a");
    expect(link).toHaveAttribute("href", "/workspaces-entry");
  });
});
