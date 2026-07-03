import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeToggle } from "./ThemeToggle";
import { THEME_KEY } from "@/lib/theme";

describe("ThemeToggle", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark", "light");
  });

  it("渲染按钮 + a11y label", () => {
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: /切换主题/ })).toBeInTheDocument();
  });

  it("dark 状态下显 ☀️（提示切到浅色）", () => {
    document.documentElement.classList.add("dark");
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: /切换主题/ }).textContent).toContain("☀️");
  });

  it("点击切换 dark→light 并持久化", () => {
    document.documentElement.classList.add("dark");
    render(<ThemeToggle />);
    fireEvent.click(screen.getByRole("button", { name: /切换主题/ }));
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
  });

  it("点击切换 light→dark", () => {
    // 初始 theme 来自 getInitialTheme（读 localStorage），需设 light 才能从 light 起步
    localStorage.setItem(THEME_KEY, "light");
    document.documentElement.classList.add("light");
    render(<ThemeToggle />);
    fireEvent.click(screen.getByRole("button", { name: /切换主题/ }));
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});
