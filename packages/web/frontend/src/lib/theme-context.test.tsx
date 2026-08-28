import { describe, it, expect, beforeEach, afterEach, vi, type MockInstance } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { ThemeProvider, useTheme } from "@/lib/theme-context";
import { THEME_KEY } from "@/lib/theme";
import { AuthContext, type AuthUser } from "@/auth/AuthContext";

function Consumer({ label }: { label: string }) {
  const { theme } = useTheme();
  return <span data-testid={label}>{theme}</span>;
}

describe("ThemeContext", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark", "light", "theme-mac", "theme-midnight", "theme-graphite");
  });

  it("Provider 初始化读 getInitialTheme（无 stored → graphite）", () => {
    render(
      <ThemeProvider>
        <Consumer label="c" />
      </ThemeProvider>
    );
    expect(screen.getByTestId("c").textContent).toBe("graphite");
  });

  it("Provider 初始化读 stored mac（palette 主题原样）", () => {
    localStorage.setItem(THEME_KEY, "mac");
    render(
      <ThemeProvider>
        <Consumer label="c" />
      </ThemeProvider>
    );
    expect(screen.getByTestId("c").textContent).toBe("mac");
  });

  it("setTheme 更新 state + 持久化 + 挂 class", () => {
    function Setter() {
      const { setTheme } = useTheme();
      return <button onClick={() => setTheme("warm-paper")}>to-light</button>;
    }
    render(
      <ThemeProvider>
        <Setter />
        <Consumer label="c" />
      </ThemeProvider>
    );
    fireEvent.click(screen.getByText("to-light"));
    expect(screen.getByTestId("c").textContent).toBe("warm-paper");
    expect(localStorage.getItem(THEME_KEY)).toBe("warm-paper");
    expect(document.documentElement.classList.contains("light")).toBe(true);
  });

  it("两消费者共享：一个 setTheme → 另一个同步读到（锁不同步 bug 不回归）", () => {
    function Setter() {
      const { setTheme } = useTheme();
      return <button onClick={() => setTheme("system")}>to-system</button>;
    }
    render(
      <ThemeProvider>
        <Consumer label="a" />
        <Consumer label="b" />
        <Setter />
      </ThemeProvider>
    );
    expect(screen.getByTestId("a").textContent).toBe("graphite");
    expect(screen.getByTestId("b").textContent).toBe("graphite");
    fireEvent.click(screen.getByText("to-system"));
    // 两个消费者都应同步到 system —— 现状两处独立 useState 时这里会失败
    expect(screen.getByTestId("a").textContent).toBe("system");
    expect(screen.getByTestId("b").textContent).toBe("system");
  });

  it("useTheme 无 Provider → 抛错（强制包裹）", () => {
    function Orphan() {
      useTheme();
      return null;
    }
    // suppress React render 错误日志
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Orphan />)).toThrow(/ThemeProvider/i);
    spy.mockRestore();
  });
});

// per-user 主题（2026-08-28）：后端为 SSOT，localStorage 降级为首帧缓存。
// 校准 = user.theme 到达时应用（不回写，避免环）；回写 = setTheme 登录态 fire-and-forget PUT。
describe("ThemeContext per-user 同步", () => {
  let fetchSpy: MockInstance<typeof globalThis.fetch>;

  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark", "light", "theme-mac", "theme-midnight", "theme-graphite");
    fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}"));
  });

  afterEach(() => fetchSpy.mockRestore());

  /** 最小合法 AuthUser（theme 可控）。 */
  const mkUser = (theme: string | null): AuthUser => ({
    id: 1, username: "admin", role: "admin", must_change_password: false, theme,
  });

  /** 假 AuthContext.Provider 注入 user（避免真 AuthProvider 的 /auth/me 网络请求）。 */
  function withUser(user: AuthUser | null, children: ReactNode) {
    return (
      <AuthContext.Provider value={{ user, loading: false, login: async () => {}, logout: async () => {}, refreshUser: async () => {} }}>
        {children}
      </AuthContext.Provider>
    );
  }

  it("user.theme 到达 → 校准应用，且不回写 PUT（无校准环）", async () => {
    render(withUser(mkUser("mac"), <ThemeProvider><Consumer label="c" /></ThemeProvider>));
    await waitFor(() => expect(screen.getByTestId("c").textContent).toBe("mac"));
    // 校准路径不得回写后端
    expect(fetchSpy.mock.calls.some((c) => String(c[0]).includes("/users/me/theme"))).toBe(false);
  });

  it("user.theme 为 null → 不校准（localStorage 首帧缓存保留）", () => {
    localStorage.setItem(THEME_KEY, "mac");
    render(withUser(mkUser(null), <ThemeProvider><Consumer label="c" /></ThemeProvider>));
    expect(screen.getByTestId("c").textContent).toBe("mac");
  });

  it("user.theme 非法值 → 不校准（防御后端脏值）", () => {
    render(withUser(mkUser("hot-dog-stand"), <ThemeProvider><Consumer label="c" /></ThemeProvider>));
    expect(screen.getByTestId("c").textContent).toBe("graphite");
  });

  it("登录态 setTheme → fire-and-forget PUT /users/me/theme", () => {
    function Setter() {
      const { setTheme } = useTheme();
      return <button onClick={() => setTheme("warm-paper")}>to-light</button>;
    }
    render(withUser(mkUser(null), <ThemeProvider><Setter /></ThemeProvider>));
    fireEvent.click(screen.getByText("to-light"));
    const put = fetchSpy.mock.calls.find((c) => String(c[0]).includes("/users/me/theme"));
    expect(put).toBeTruthy();
    expect((put![1] as RequestInit | undefined)?.method).toBe("PUT");
  });

  it("未登录 setTheme → 不发 PUT（localStorage 仍持久化）", () => {
    function Setter() {
      const { setTheme } = useTheme();
      return <button onClick={() => setTheme("warm-paper")}>to-light</button>;
    }
    render(<ThemeProvider><Setter /></ThemeProvider>);
    fireEvent.click(screen.getByText("to-light"));
    expect(localStorage.getItem(THEME_KEY)).toBe("warm-paper");
    expect(fetchSpy.mock.calls.some((c) => String(c[0]).includes("/users/me/theme"))).toBe(false);
  });
});
