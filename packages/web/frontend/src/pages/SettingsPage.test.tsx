import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup, act } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import { SettingsPage } from "./SettingsPage";

// SettingsPage 用 useAuth 取 must_change_password / refreshUser。本测试聚焦系统状态
// 渲染，stub 掉 AuthContext 避免必须包 AuthProvider + mock /auth/me。
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: { id: 1, username: "admin", role: "admin", must_change_password: false },
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
  }),
}));

const okBody = {
  ai_provider: "claude",
  browser_engine: "agent-browser",
  temporal: { enabled: true, host: "localhost:7233", last_status: "connected", last_error: null },
  git: { binary_available: true, credentials_configured: true },
  version: "supernova-web 0.1.0",
};

const server = setupServer(
  http.get("/api/system-status", () => HttpResponse.json(okBody)),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
// jsdom navigator.language 默认 en，LanguageDetector 会把 i18n 切到 en；现有断言依赖中文渲染，逐测试钉回 zh。
beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

describe("SettingsPage", () => {
  it("渲染三张 Card(主题/系统状态/关于)", async () => {
    render(<SettingsPage />);
    // CardTitle 渲染为 div(非语义 heading),用文本匹配
    expect(await screen.findByText("主题")).toBeInTheDocument();
    expect(screen.getByText("系统状态")).toBeInTheDocument();
    expect(screen.getByText("关于")).toBeInTheDocument();
  });

  it("状态面板渲染各字段(ai_provider/temporal/version)", async () => {
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByText("claude")).toBeInTheDocument());
    expect(screen.getByText("agent-browser")).toBeInTheDocument();
    expect(screen.getByText("localhost:7233")).toBeInTheDocument();
    expect(screen.getByText("supernova-web 0.1.0")).toBeInTheDocument();
    // git 拆成两个独立信号(二进制 / GitLab 凭据)
    expect(screen.getByText("已装")).toBeInTheDocument(); // git 二进制
    expect(screen.getByText("已配置")).toBeInTheDocument(); // GitLab 凭据
  });

  it("GitLab 凭据未配置 → 显示未配置提示(本地路径模式无需)", async () => {
    server.use(http.get("/api/system-status", () => HttpResponse.json({
      ...okBody,
      git: { binary_available: true, credentials_configured: false },
    })));
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByText(/未配置/)).toBeInTheDocument());
  });

  it("主题 Switch 切到浅色 → <html> 加 light class + localStorage", async () => {
    render(<SettingsPage />);
    const sw = screen.getByRole("switch", { name: /切换深浅主题/ });
    fireEvent.click(sw);
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(localStorage.getItem("supernova-theme")).toBe("light");
  });

  it("status fetch 失败 → 局部 ErrorState(role=alert)", async () => {
    server.use(http.get("/api/system-status", () => HttpResponse.json({}, { status: 500 })));
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    // 主题 Card 仍在(不受 status 失败影响)
    expect(screen.getByText("主题")).toBeInTheDocument();
  });
});

describe("SettingsPage i18n", () => {
  afterEach(() => i18n.changeLanguage("zh"));

  it("中文渲染页标题与三张 Card 标题", async () => {
    render(<SettingsPage />);
    expect(await screen.findByText("设置")).toBeInTheDocument();
    expect(screen.getByText("主题")).toBeInTheDocument();
    expect(screen.getByText("系统状态")).toBeInTheDocument();
    expect(screen.getByText("关于")).toBeInTheDocument();
  });

  it("切英文后页标题变 Settings + Card 标题变 Theme/System status/About", async () => {
    render(<SettingsPage />);
    await screen.findByText("设置");
    await act(async () => {
      await i18n.changeLanguage("en");
    });
    expect(await screen.findByText("Settings")).toBeInTheDocument();
    expect(screen.getByText("Theme")).toBeInTheDocument();
    expect(screen.getByText("System status")).toBeInTheDocument();
    expect(screen.getByText("About")).toBeInTheDocument();
  });
});
