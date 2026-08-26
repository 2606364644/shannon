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

// ---- SSO（spec 2026-08-25 §8）----
// jsdom 的 location.assign 属性不可配置，vi.spyOn 会抛 "Cannot redefine property"，
// 沿 client.test.ts 既有模式整对象替换 window.location（pathname 钉 /login，兼
// default handler 的「已在 /login 不重复跳转」防御）；用完 restore 恢复原对象。
function mockLocationAssign(): { assign: ReturnType<typeof vi.fn>; restore: () => void } {
  const assign = vi.fn();
  const origLoc = window.location;
  Object.defineProperty(window, "location", {
    value: { pathname: "/login", assign } as unknown as Location,
    writable: true,
    configurable: true,
  });
  return {
    assign,
    restore: () => Object.defineProperty(window, "location", { value: origLoc, writable: true, configurable: true }),
  };
}

// 按 URL 子串分流：命中 key 返回其 JSON；其余 401（/auth/me 未登录）
function mockFetchByRoute(map: Record<string, unknown>) {
  vi.spyOn(window, "fetch").mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : (input as Request).url;
    for (const [k, v] of Object.entries(map)) {
      if (url.includes(k)) return Promise.resolve(new Response(JSON.stringify(v), { status: 200 }));
    }
    return Promise.resolve(new Response("{}", { status: 401 }));
  });
}

describe("LoginPage", () => {
  // jsdom navigator.language 默认 en-US，i18n LanguageDetector 会渲染英文，
  // 本测试断言中文文案，故每个测试钉回 zh（遵循 App.test.tsx / WorkspaceListPage.test.tsx 既有模式）。
  beforeEach(() => {
    i18n.changeLanguage("zh");
    localStorage.clear();
    document.title = "";
    document.documentElement.classList.remove("dark", "light", "theme-mac", "theme-midnight", "theme-graphite");
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
    expect(localStorage.getItem(THEME_KEY)).toBe("warm-paper"); // 对侧默认主题=warm-paper（2026-08-27 默认浅色回切品牌基准）
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

  it("登录被锁定（429）时显示锁定提示而非笼统 Login failed", async () => {
    // BruteGuard：5 次失败锁 5 分钟，期间正确密码也 429。此前前端只区分 401，
    // 429 落进兜底 "Login failed"，用户误以为密码错继续试 → 恶性循环。
    const fetchSpy = vi.spyOn(window, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url.includes("/api/auth/login")) {
        return new Response(JSON.stringify({ detail: "too many attempts, try later" }), { status: 429 });
      }
      if (url.includes("/api/auth/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "tok" }), { status: 200 });
      }
      return new Response("{}", { status: 401 }); // /auth/me
    });
    wrap();
    await waitFor(() => expect(screen.getByText("欢迎回来")).toBeTruthy());
    await act(async () => {
      fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "bob" } });
      fireEvent.change(screen.getByLabelText("密码"), { target: { value: "whatever" } });
      fireEvent.click(screen.getByRole("button", { name: "登录" }));
    });
    await waitFor(() => expect(screen.getByText(/临时锁定/)).toBeTruthy());
    expect(screen.queryByText("Login failed")).toBeNull();
    fetchSpy.mockRestore();
  });

  it("SSO enabled 时渲染 OA 登录按钮并跳转 sso/login", async () => {
    mockFetchByRoute({ "/auth/sso/config": { enabled: true } });
    const { assign, restore } = mockLocationAssign();
    wrap();
    const btn = await screen.findByTestId("sso-login-btn");
    expect(btn.textContent).toContain("使用 OA 账号登录");
    fireEvent.click(btn);
    expect(assign).toHaveBeenCalledWith("/api/auth/sso/login?next=%2F");
    restore();
  });

  it("SSO disabled 时不渲染按钮", async () => {
    // config 端点 401/不可达 → 按 disabled 处理，不渲染 OA 按钮、不阻塞账密表单
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 401 }));
    wrap();
    await waitFor(() => expect(screen.getByText("欢迎回来")).toBeTruthy());
    expect(screen.queryByTestId("sso-login-btn")).toBeNull();
  });

  it("sso_error=not_whitelisted 显示未授权文案", async () => {
    mockFetchByRoute({ "/auth/sso/config": { enabled: true } });
    render(
      <BrandProvider><AuthProvider><ThemeProvider>
        <MemoryRouter initialEntries={["/login?sso_error=not_whitelisted"]}>
          <Routes><Route path="/login" element={<LoginPage />} /></Routes>
        </MemoryRouter>
      </ThemeProvider></AuthProvider></BrandProvider>
    );
    expect(await screen.findByText("账号未授权，请联系管理员开通")).toBeTruthy();
  });

  it("sso_error 其他 code 显示通用失败文案", async () => {
    mockFetchByRoute({ "/auth/sso/config": { enabled: true } });
    render(
      <BrandProvider><AuthProvider><ThemeProvider>
        <MemoryRouter initialEntries={["/login?sso_error=upstream_error"]}>
          <Routes><Route path="/login" element={<LoginPage />} /></Routes>
        </MemoryRouter>
      </ThemeProvider></AuthProvider></BrandProvider>
    );
    expect(await screen.findByText("SSO 登录失败，请重试")).toBeTruthy();
  });
});
