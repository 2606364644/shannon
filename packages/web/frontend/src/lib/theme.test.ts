import { describe, it, expect, beforeEach } from "vitest";
import { applyTheme, getInitialTheme, THEME_KEY } from "@/lib/theme";

describe("theme lib", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark", "light");
  });

  it("THEME_KEY = shannon-theme", () => {
    expect(THEME_KEY).toBe("shannon-theme");
  });

  it("getInitialTheme: localStorage 优先", () => {
    localStorage.setItem(THEME_KEY, "light");
    expect(getInitialTheme()).toBe("light");
  });

  it("getInitialTheme: 无 stored → 读 prefers-color-scheme（stub matches=false → dark）", () => {
    expect(getInitialTheme()).toBe("dark");
  });

  it("getInitialTheme: 非法 stored 值回退", () => {
    localStorage.setItem(THEME_KEY, "purple");
    expect(getInitialTheme()).toBe("dark");
  });

  it("applyTheme(light): 写 <html>.light + localStorage", () => {
    applyTheme("light");
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
  });

  it("applyTheme: 切换时清旧 class", () => {
    applyTheme("light");
    applyTheme("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.classList.contains("light")).toBe(false);
  });
});
