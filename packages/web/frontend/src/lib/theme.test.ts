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

  it("getInitialTheme: 无 stored → 读 prefers-color-scheme（stub matches=false → graphite）", () => {
    expect(getInitialTheme()).toBe("graphite");
  });

  it("getInitialTheme: 非法 stored 值回退 graphite", () => {
    localStorage.setItem(THEME_KEY, "purple");
    expect(getInitialTheme()).toBe("graphite");
  });

  it("applyTheme(warm-paper): 写 <html>.light + localStorage", () => {
    applyTheme("warm-paper");
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(localStorage.getItem(THEME_KEY)).toBe("warm-paper");
    // 2026-08-26 亮色材质升级：warm-paper 获材质专用 palette class（色 token 仍在 .light 基础块）
    expect(document.documentElement.classList.contains("theme-warm-paper")).toBe(true);
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

  it("THEMES 覆盖 13 主题；palette id 与 paletteClass 一一对应；基础对 charcoal/warm-paper 各组在前", () => {
    expect(THEMES.map((t) => t.id)).toEqual([
      "charcoal", "midnight", "graphite", "sentry", "arc", "mission",
      "warm-paper", "mac", "github", "notion", "kami", "blueprint", "openai",
    ]);
    expect(getThemeDef("charcoal")?.paletteClass).toBeNull();
    expect(getThemeDef("mac")?.paletteClass).toBe("theme-mac");
    expect(getThemeDef("sentry")?.paletteClass).toBe("theme-sentry");
    expect(getThemeDef("arc")?.paletteClass).toBe("theme-arc");
    expect(getThemeDef("mission")?.paletteClass).toBe("theme-mission");
    expect(getThemeDef("github")?.paletteClass).toBe("theme-github");
    expect(getThemeDef("notion")?.paletteClass).toBe("theme-notion");
    expect(getThemeDef("kami")?.paletteClass).toBe("theme-kami");
    // 2026-08-26 亮色材质升级：warm-paper 材质专用块 + 新增 blueprint
    expect(getThemeDef("warm-paper")?.paletteClass).toBe("theme-warm-paper");
    expect(getThemeDef("blueprint")?.paletteClass).toBe("theme-blueprint");
    // 2026-08-27 OpenAI 主题（OpenDesign design-system-openai 移植）
    expect(getThemeDef("openai")?.paletteClass).toBe("theme-openai");
    expect(getThemeDef("system")).toBeNull();
  });

  it("oppositeBaseTheme: dark→openai / light→graphite（翻到对侧默认主题）", () => {
    // 2026-08-27 默认主题对调整：深色=graphite / 浅色=openai（用户决策，同日第二次调默认）
    expect(oppositeBaseTheme("dark")).toBe("openai");
    expect(oppositeBaseTheme("light")).toBe("graphite");
  });

  it("applyTheme(sentry/arc/mission): dark + 各自 palette class", () => {
    for (const id of ["sentry", "arc", "mission"] as const) {
      applyTheme(id);
      const cl = document.documentElement.classList;
      expect(cl.contains("dark")).toBe(true);
      expect(cl.contains(`theme-${id}`)).toBe(true);
    }
  });

  it("applyTheme(github/notion/kami/blueprint/openai): light + 各自 palette class", () => {
    for (const id of ["github", "notion", "kami", "blueprint", "openai"] as const) {
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
    localStorage.setItem(THEME_KEY, "blueprint");
    expect(getInitialTheme()).toBe("blueprint");
    localStorage.setItem(THEME_KEY, "openai");
    expect(getInitialTheme()).toBe("openai");
  });

  it("resolveEffectiveTheme: 新主题查 def.mode 正确", () => {
    expect(resolveEffectiveTheme("sentry")).toBe("dark");
    expect(resolveEffectiveTheme("arc")).toBe("dark");
    expect(resolveEffectiveTheme("mission")).toBe("dark");
    expect(resolveEffectiveTheme("github")).toBe("light");
    expect(resolveEffectiveTheme("notion")).toBe("light");
    expect(resolveEffectiveTheme("kami")).toBe("light");
    expect(resolveEffectiveTheme("blueprint")).toBe("light");
    expect(resolveEffectiveTheme("openai")).toBe("light");
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

  it("applyTheme(system): 存 system + 挂 effective class（系统深色→dark，落默认深色 graphite 的 palette）", () => {
    mockMatchMedia(false);
    applyTheme("system");
    expect(localStorage.getItem(THEME_KEY)).toBe("system");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.classList.contains("theme-graphite")).toBe(true);
  });

  it("applyTheme(system): 系统浅色 → light + theme-openai（默认亮色=openai）", () => {
    mockMatchMedia(true);
    applyTheme("system");
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(document.documentElement.classList.contains("theme-openai")).toBe(true);
  });

  it("getInitialTheme: 无 stored + 系统浅色 → openai（默认亮色主题）", () => {
    mockMatchMedia(true);
    expect(getInitialTheme()).toBe("openai");
  });

  it("applyTheme(system): 注册 matchMedia change 监听", () => {
    const mm = mockMatchMedia(false);
    applyTheme("system");
    expect(mm.listenerCount()).toBe(1);
  });

  it("system 下系统偏好变化 → 实时重挂 class + 切默认主题 palette（dark=theme-graphite / light=theme-openai）", () => {
    const mm = mockMatchMedia(false);
    applyTheme("system");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    mm.change(true); // 系统切浅色 → 默认亮色 openai
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(document.documentElement.classList.contains("theme-openai")).toBe(true);
    mm.change(false); // 切回深色 → 默认深色 graphite
    expect(document.documentElement.classList.contains("theme-openai")).toBe(false);
    expect(document.documentElement.classList.contains("theme-graphite")).toBe(true);
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
