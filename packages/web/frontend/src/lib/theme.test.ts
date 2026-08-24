import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  applyTheme,
  getInitialTheme,
  getThemeDef,
  oppositeBaseTheme,
  resolveEffectiveTheme,
  THEMES,
  THEME_KEY,
} from "@/lib/theme";

function clearHtmlClass() {
  document.documentElement.classList.remove("dark", "light", ...THEMES.map((t) => t.paletteClass).filter(Boolean) as string[]);
}

describe("theme lib", () => {
  beforeEach(() => {
    localStorage.clear();
    clearHtmlClass();
  });

  it("THEME_KEY = supernova-theme", () => {
    expect(THEME_KEY).toBe("supernova-theme");
  });

  it("getInitialTheme: localStorage 优先（新 ThemeId 原样）", () => {
    localStorage.setItem(THEME_KEY, "mac");
    expect(getInitialTheme()).toBe("mac");
  });

  it("getInitialTheme: 旧值 dark/light 读时归一为 charcoal/warm-paper", () => {
    localStorage.setItem(THEME_KEY, "dark");
    expect(getInitialTheme()).toBe("charcoal");
    localStorage.setItem(THEME_KEY, "light");
    expect(getInitialTheme()).toBe("warm-paper");
  });

  it("getInitialTheme: 旧值 frost（霜白已改名）读时归一为 mac（只读时迁移，不回写）", () => {
    localStorage.setItem(THEME_KEY, "frost");
    expect(getInitialTheme()).toBe("mac");
    expect(localStorage.getItem(THEME_KEY)).toBe("frost");
  });

  it("getInitialTheme: 无 stored → 读 prefers-color-scheme（stub matches=false → charcoal）", () => {
    expect(getInitialTheme()).toBe("charcoal");
  });

  it("getInitialTheme: 非法 stored 值回退 charcoal", () => {
    localStorage.setItem(THEME_KEY, "purple");
    expect(getInitialTheme()).toBe("charcoal");
  });

  it("applyTheme(warm-paper): 写 <html>.light + localStorage", () => {
    applyTheme("warm-paper");
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(localStorage.getItem(THEME_KEY)).toBe("warm-paper");
  });

  it("applyTheme: 切换时清旧 class（含 palette class）", () => {
    applyTheme("mac");
    expect(document.documentElement.classList.contains("theme-mac")).toBe(true);
    applyTheme("charcoal");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.classList.contains("light")).toBe(false);
    expect(document.documentElement.classList.contains("theme-mac")).toBe(false);
  });

  it("applyTheme(mac): mode class + palette class 同挂（驱动 dark: variant 与 token 覆盖）", () => {
    applyTheme("mac");
    const cl = document.documentElement.classList;
    expect(cl.contains("light")).toBe(true);
    expect(cl.contains("theme-mac")).toBe(true);
  });

  it("applyTheme(midnight): dark + theme-midnight", () => {
    applyTheme("midnight");
    const cl = document.documentElement.classList;
    expect(cl.contains("dark")).toBe(true);
    expect(cl.contains("theme-midnight")).toBe(true);
    expect(cl.contains("light")).toBe(false);
  });

  it("THEMES 覆盖 11 主题；palette id 与 paletteClass 一一对应；浅色组默认 Mac 在前", () => {
    expect(THEMES.map((t) => t.id)).toEqual([
      "charcoal", "midnight", "graphite", "sentry", "arc", "mission",
      "mac", "warm-paper", "github", "notion", "kami",
    ]);
    expect(getThemeDef("charcoal")?.paletteClass).toBeNull();
    expect(getThemeDef("mac")?.paletteClass).toBe("theme-mac");
    expect(getThemeDef("sentry")?.paletteClass).toBe("theme-sentry");
    expect(getThemeDef("arc")?.paletteClass).toBe("theme-arc");
    expect(getThemeDef("mission")?.paletteClass).toBe("theme-mission");
    expect(getThemeDef("github")?.paletteClass).toBe("theme-github");
    expect(getThemeDef("notion")?.paletteClass).toBe("theme-notion");
    expect(getThemeDef("kami")?.paletteClass).toBe("theme-kami");
    expect(getThemeDef("system")).toBeNull();
  });

  it("oppositeBaseTheme: dark→mac / light→charcoal（翻到对侧默认主题）", () => {
    expect(oppositeBaseTheme("dark")).toBe("mac");
    expect(oppositeBaseTheme("light")).toBe("charcoal");
  });

  it("applyTheme(sentry/arc/mission): dark + 各自 palette class", () => {
    for (const id of ["sentry", "arc", "mission"] as const) {
      applyTheme(id);
      const cl = document.documentElement.classList;
      expect(cl.contains("dark")).toBe(true);
      expect(cl.contains(`theme-${id}`)).toBe(true);
    }
  });

  it("applyTheme(github/notion/kami): light + 各自 palette class", () => {
    for (const id of ["github", "notion", "kami"] as const) {
      applyTheme(id);
      const cl = document.documentElement.classList;
      expect(cl.contains("light")).toBe(true);
      expect(cl.contains(`theme-${id}`)).toBe(true);
    }
  });

  it("getInitialTheme: 新主题 id stored 原样读出", () => {
    localStorage.setItem(THEME_KEY, "kami");
    expect(getInitialTheme()).toBe("kami");
    localStorage.setItem(THEME_KEY, "mission");
    expect(getInitialTheme()).toBe("mission");
  });

  it("resolveEffectiveTheme: 新主题查 def.mode 正确", () => {
    expect(resolveEffectiveTheme("sentry")).toBe("dark");
    expect(resolveEffectiveTheme("arc")).toBe("dark");
    expect(resolveEffectiveTheme("mission")).toBe("dark");
    expect(resolveEffectiveTheme("github")).toBe("light");
    expect(resolveEffectiveTheme("notion")).toBe("light");
    expect(resolveEffectiveTheme("kami")).toBe("light");
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
    clearHtmlClass();
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

  it("resolveEffectiveTheme: 显式主题查 def.mode", () => {
    expect(resolveEffectiveTheme("charcoal")).toBe("dark");
    expect(resolveEffectiveTheme("mac")).toBe("light");
    expect(resolveEffectiveTheme("midnight")).toBe("dark");
    expect(resolveEffectiveTheme("warm-paper")).toBe("light");
  });

  it("applyTheme(system): 存 system + 挂 effective class（系统深色→dark，落默认深色 charcoal 无 palette）", () => {
    mockMatchMedia(false);
    applyTheme("system");
    expect(localStorage.getItem(THEME_KEY)).toBe("system");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.className).not.toContain("theme-");
  });

  it("applyTheme(system): 系统浅色 → light + theme-mac（默认亮色=Mac）", () => {
    mockMatchMedia(true);
    applyTheme("system");
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(document.documentElement.classList.contains("theme-mac")).toBe(true);
  });

  it("getInitialTheme: 无 stored + 系统浅色 → mac（默认亮色主题）", () => {
    mockMatchMedia(true);
    expect(getInitialTheme()).toBe("mac");
  });

  it("applyTheme(system): 注册 matchMedia change 监听", () => {
    const mm = mockMatchMedia(false);
    applyTheme("system");
    expect(mm.listenerCount()).toBe(1);
  });

  it("system 下系统偏好变化 → 实时重挂 class + 切默认主题 palette（dark 无 / light=theme-mac）", () => {
    const mm = mockMatchMedia(false);
    applyTheme("system");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    mm.change(true); // 系统切浅色 → 默认亮色 Mac
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(document.documentElement.classList.contains("theme-mac")).toBe(true);
    mm.change(false); // 切回深色 → palette 清回 charcoal 基础态
    expect(document.documentElement.classList.contains("theme-mac")).toBe(false);
  });

  it("切回显式态 → 清理 system 监听（无泄漏）", () => {
    const mm = mockMatchMedia(false);
    applyTheme("system");
    expect(mm.listenerCount()).toBe(1);
    applyTheme("charcoal");
    expect(mm.listenerCount()).toBe(0);
  });

  it("getInitialTheme: stored system → 返回 system", () => {
    localStorage.setItem(THEME_KEY, "system");
    expect(getInitialTheme()).toBe("system");
  });
});
