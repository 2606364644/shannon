import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeProvider, useTheme } from "@/lib/theme-context";
import { THEME_KEY } from "@/lib/theme";

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
