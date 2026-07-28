import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { applyTheme, getInitialTheme, resolveEffectiveTheme, THEME_KEY } from "@/lib/theme";

describe("theme lib", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark", "light");
  });

  it("THEME_KEY = supernova-theme", () => {
    expect(THEME_KEY).toBe("supernova-theme");
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

describe("theme system 态", () => {
  // test-setup 的 matchMedia 是静态 stub（matches=false、空 listener）；
  // system 监听测试需可控 matchMedia：能记录 listener、能改 matches 触发 change。
  function mockMatchMedia(matches: boolean) {
    const listeners = new Set<(e: { matches: boolean }) => void>();
    const mql = {
      matches,
      media: "(prefers-color-scheme: light)",
      onchange: null,
      addEventListener: (_e: string, l: (e: { matches: boolean }) => void) => { listeners.add(l); },
      removeEventListener: (_e: string, l: (e: { matches: boolean }) => void) => { listeners.delete(l); },
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    };
    vi.spyOn(window, "matchMedia").mockReturnValue(mql as unknown as MediaQueryList);
    return {
      change: (next: boolean) => {
        (mql as { matches: boolean }).matches = next;
        listeners.forEach((l) => l({ matches: next }));
      },
      listenerCount: () => listeners.size,
    };
  }

  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark", "light");
  });
  afterEach(() => vi.restoreAllMocks());

  it("resolveEffectiveTheme: system + 系统浅色 → light", () => {
    mockMatchMedia(true);
    expect(resolveEffectiveTheme("system")).toBe("light");
  });

  it("resolveEffectiveTheme: system + 系统深色 → dark", () => {
    mockMatchMedia(false);
    expect(resolveEffectiveTheme("system")).toBe("dark");
  });

  it("resolveEffectiveTheme: 显式态原样返回", () => {
    expect(resolveEffectiveTheme("dark")).toBe("dark");
    expect(resolveEffectiveTheme("light")).toBe("light");
  });

  it("applyTheme(system): 存 system + 挂 effective class（系统深色→dark）", () => {
    mockMatchMedia(false);
    applyTheme("system");
    expect(localStorage.getItem(THEME_KEY)).toBe("system");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("applyTheme(system): 注册 matchMedia change 监听", () => {
    const mm = mockMatchMedia(false);
    applyTheme("system");
    expect(mm.listenerCount()).toBe(1);
  });

  it("system 下系统偏好变化 → 实时重挂 class", () => {
    const mm = mockMatchMedia(false);
    applyTheme("system");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    mm.change(true); // 系统切浅色
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("切回显式态 → 清理 system 监听（无泄漏）", () => {
    const mm = mockMatchMedia(false);
    applyTheme("system");
    expect(mm.listenerCount()).toBe(1);
    applyTheme("dark");
    expect(mm.listenerCount()).toBe(0);
  });

  it("getInitialTheme: stored system → 返回 system", () => {
    localStorage.setItem(THEME_KEY, "system");
    expect(getInitialTheme()).toBe("system");
  });
});
