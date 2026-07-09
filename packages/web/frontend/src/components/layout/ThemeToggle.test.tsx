import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import i18n from "@/i18n";
import { ThemeToggle } from "./ThemeToggle";
import { THEME_KEY } from "@/lib/theme";

describe("ThemeToggle", () => {
  beforeEach(() => {
    i18n.changeLanguage("zh");
    localStorage.clear();
    document.documentElement.classList.remove("dark", "light");
  });
  afterEach(() => i18n.changeLanguage("zh"));

  it("渲染按钮 + a11y label", () => {
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: /切换主题/ })).toBeInTheDocument();
  });

  it("dark 状态下显 Sun 图标（提示切到浅色）", () => {
    document.documentElement.classList.add("dark");
    const { container } = render(<ThemeToggle />);
    // lucide-react Sun 渲染为 svg.lucide-sun（替代原 ☀️ emoji）
    expect(container.querySelector("svg.lucide-sun")).not.toBeNull();
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

  it("用 lucide svg 图标而非 emoji", () => {
    const { container } = render(<ThemeToggle />);
    // 含 svg（lucide 渲染 svg），不含 ☀️/🌙 emoji
    expect(container.querySelector("svg")).not.toBeNull();
    expect(container.textContent ?? "").not.toMatch(/[☀️🌙]/);
  });

  it("i18n: 切英文 a11y label/title 为英文", () => {
    i18n.changeLanguage("en");
    localStorage.setItem(THEME_KEY, "dark");
    document.documentElement.classList.add("dark");
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: /Toggle theme/i })).toBeInTheDocument();
    // dark 状态下 title 提示切到浅色
    expect(screen.getByRole("button")).toHaveAttribute("title", "Switch to light");
  });
});
