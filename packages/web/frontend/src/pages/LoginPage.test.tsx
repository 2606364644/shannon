import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { AuthProvider } from "@/auth/AuthContext";
import { BrandProvider } from "@/brand/BrandContext";
import { ThemeProvider } from "@/lib/theme-context";
import i18n from "@/i18n";
import LoginPage from "./LoginPage";
import { THEME_KEY } from "@/lib/theme";

function wrap() {
  return render(
    <BrandProvider>
      <AuthProvider>
        <ThemeProvider>
          <MemoryRouter initialEntries={["/login"]}>
            <Routes><Route path="/login" element={<LoginPage />} /></Routes>
          </MemoryRouter>
        </ThemeProvider>
      </AuthProvider>
    </BrandProvider>
  );
}

describe("LoginPage", () => {
  // jsdom navigator.language 默认 en-US，i18n LanguageDetector 会渲染英文，
  // 本测试断言中文文案，故每个测试钉回 zh（遵循 App.test.tsx / WorkspaceListPage.test.tsx 既有模式）。
  beforeEach(() => {
    i18n.changeLanguage("zh");
    localStorage.clear();
    document.title = "";
    document.documentElement.classList.remove("dark", "light");
  });
  afterEach(() => i18n.changeLanguage("zh"));

  it("渲染欢迎标题与表单（未登录态）", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 401 })); // /auth/me 401
    wrap();
    await waitFor(() => expect(screen.getByText("欢迎回来")).toBeTruthy());
    expect(screen.getByLabelText("用户名")).toBeTruthy();
    expect(screen.getByLabelText("密码")).toBeTruthy();
    expect(screen.getByRole("button", { name: "登录" })).toBeTruthy();
  });

  it("不强制锁定亮主题（根容器无 light class）", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 401 }));
    const { container } = wrap();
    await waitFor(() => expect(screen.getByText("欢迎回来")).toBeTruthy());
    // 根容器是渲染输出的第一个 div
    const root = container.firstElementChild;
    expect(root?.className ?? "").not.toMatch(/\blight\b/);
  });

  it("登录页内提供主题切换开关，点击翻转 <html> 主题", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 401 }));
    // 起步设深色
    localStorage.setItem(THEME_KEY, "dark");
    document.documentElement.classList.add("dark");
    wrap();
    const toggle = await screen.findByRole("button", { name: /切换主题/ });
    await act(async () => {
      fireEvent.click(toggle);
    });
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
  });

  it("跟随全局主题（<html> 设 dark 时不强制 light）", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 401 }));
    document.documentElement.classList.add("dark");
    const { container } = wrap();
    await waitFor(() => expect(screen.getByText("欢迎回来")).toBeTruthy());
    const root = container.firstElementChild;
    expect(root?.className ?? "").not.toMatch(/\blight\b/);
  });

  it("左品牌区字标跟随注入的品牌名（非硬编码 Supernova）", async () => {
    // 模拟生产 index.html：后端 _render_index_html 已把生效品牌名注入 <title>
    // （BrandProvider 初始 state 继承 document.title，未登录 /login 页 system-status 401 也能拿到）。
    document.title = "ft-codesec";
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 401 }));
    wrap();
    await waitFor(() => expect(screen.getByText("欢迎回来")).toBeTruthy());
    // 左品牌区字标 = 注入的品牌名，而非硬编码 "Supernova"
    expect(screen.getByText("ft-codesec")).toBeTruthy();
  });
});
