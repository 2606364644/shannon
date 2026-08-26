import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import i18n from "@/i18n";
import { ThemeToggle } from "./ThemeToggle";
import { ThemeProvider } from "@/lib/theme-context";
import { THEME_KEY } from "@/lib/theme";

function renderToggle() {
  return render(
    <ThemeProvider>
      <ThemeToggle />
    </ThemeProvider>
  );
}

describe("ThemeToggle", () => {
  beforeEach(async () => {
    await act(async () => { await i18n.changeLanguage("zh"); });
    localStorage.clear();
    document.documentElement.classList.remove("dark", "light", "theme-mac", "theme-midnight", "theme-graphite");
  });
  afterEach(async () => {
    await act(async () => { await i18n.changeLanguage("zh"); });
  });

  it("渲染按钮 + a11y label", () => {
    renderToggle();
    expect(screen.getByRole("button", { name: /切换主题/ })).toBeInTheDocument();
  });

  it("dark 态显 Sun 图标（提示切到浅色）", () => {
    localStorage.setItem(THEME_KEY, "dark");
    const { container } = renderToggle();
    expect(container.querySelector("svg.lucide-sun")).not.toBeNull();
  });

  it("点击 dark→light 并持久化（对侧默认主题 warm-paper，挂 theme-warm-paper）", () => {
    localStorage.setItem(THEME_KEY, "dark"); // 旧值 → 读时归一 charcoal
    renderToggle();
    fireEvent.click(screen.getByRole("button", { name: /切换主题/ }));
    expect(document.documentElement.classList.contains("light")).toBe(true);
    // 2026-08-27 默认主题对回切：mac 果味修订（Apple 蓝主色）后默认浅色回品牌基准
    // warm-paper（coral），快捷翻转落点同步（spec 2026-08-27-mac-theme-apple-flavor）
    expect(document.documentElement.classList.contains("theme-warm-paper")).toBe(true);
    expect(localStorage.getItem(THEME_KEY)).toBe("warm-paper");
  });

  it("点击 light→dark（对侧基础主题 charcoal）", () => {
    localStorage.setItem(THEME_KEY, "light"); // 旧值 → 读时归一 warm-paper
    renderToggle();
    fireEvent.click(screen.getByRole("button", { name: /切换主题/ }));
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(localStorage.getItem(THEME_KEY)).toBe("charcoal");
  });

  it("palette 主题（mac）点击 → 翻到对侧基础主题 charcoal（无对侧变体）", () => {
    localStorage.setItem(THEME_KEY, "mac");
    renderToggle();
    expect(document.documentElement.classList.contains("theme-mac")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /切换主题/ }));
    expect(localStorage.getItem(THEME_KEY)).toBe("charcoal");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.classList.contains("theme-mac")).toBe(false);
  });

  it("system 态点击 → 落到显式反色（effective dark → warm-paper，退出 system）", () => {
    localStorage.setItem(THEME_KEY, "system");
    // test-setup matchMedia stub matches=false → effective dark
    renderToggle();
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /切换主题/ }));
    expect(localStorage.getItem(THEME_KEY)).toBe("warm-paper");
    expect(document.documentElement.classList.contains("light")).toBe(true);
  });

  it("用 lucide svg 图标而非 emoji", () => {
    const { container } = renderToggle();
    expect(container.querySelector("svg")).not.toBeNull();
    expect(container.textContent ?? "").not.toMatch(/[☀️🌙]/);
  });

  it("i18n: 切英文 a11y label/title 为英文", async () => {
    localStorage.setItem(THEME_KEY, "dark");
    renderToggle();
    await act(async () => { await i18n.changeLanguage("en"); });
    expect(screen.getByRole("button", { name: /Toggle theme/i })).toBeInTheDocument();
    expect(screen.getByRole("button")).toHaveAttribute("title", "Switch to light");
  });
});
