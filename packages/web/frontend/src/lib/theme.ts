/* 主题模型：一维 ThemeId（每主题自带 mode + palette class）。
   html class 挂两层：mode class（dark|light，驱动 tailwind dark: variant 与
   report.css .light hljs 覆盖）+ 可选 palette class（theme-*，在 tokens.css
   内以 .dark.theme-midnight / .light.theme-mac 双类选择器覆盖 token，
   特异性 (0,2,0) 严格高于 :root/.light 单类，不依赖源顺序）。
   localStorage 旧值 "dark"/"light" 读时映射为 "charcoal"/"warm-paper"；
   "frost"（霜白，2026-08-24 改名 Mac）读时映射为 "mac"（只读时归一，不回写）。
   默认主题对（2026-08-24）：浅色=Mac、深色=Claude 深色（charcoal）——首次访问按
   OS 偏好落这一对，system 态与快捷翻转（oppositeBaseTheme）同源。 */

export type ThemeMode = "dark" | "light";
export type ThemeId = "system" | "charcoal" | "warm-paper" | "mac" | "midnight" | "graphite";
/** @deprecated 语义由 ThemeMode 取代；保留别名避免存量导入破坏。 */
export type EffectiveTheme = ThemeMode;
export type Theme = ThemeId;
export const THEME_KEY = "supernova-theme";

const MQ = "(prefers-color-scheme: light)";

export interface ThemeDef {
  id: Exclude<ThemeId, "system">;
  mode: ThemeMode;
  /** tokens.css 的 palette 覆盖 class；null = 基础主题（:root 深默认 / .light 浅默认）。 */
  paletteClass: string | null;
  /** SettingsPage 色卡预览：硬编码 hsl 真值（与 tokens.css 对应块同步维护），
      不消费 CSS var —— var 随当前主题变，色卡必须恒定展示各主题本色。 */
  preview: { bg: string; card: string; primary: string; border: string };
}

/** 全部主题：深色组在前、浅色组在后（SettingsPicker 按组分块渲染；
    浅色组默认主题 mac 排最前）。charcoal/warm-paper = Claude 风深/浅基础主题
    （tokens.css :root/.light 精确对齐 claude.ai 真值色）。 */
export const THEMES: readonly ThemeDef[] = [
  {
    id: "charcoal",
    mode: "dark",
    paletteClass: null,
    preview: { bg: "hsl(60 3% 15%)", card: "hsl(60 3% 18%)", primary: "hsl(15 60% 56%)", border: "hsl(36 10% 90% / 0.10)" },
  },
  {
    id: "midnight",
    mode: "dark",
    paletteClass: "theme-midnight",
    preview: { bg: "hsl(230 20% 8%)", card: "hsl(230 18% 11%)", primary: "hsl(16 70% 62%)", border: "hsl(230 30% 80% / 0.14)" },
  },
  {
    id: "graphite",
    mode: "dark",
    paletteClass: "theme-graphite",
    preview: { bg: "hsl(0 0% 5%)", card: "hsl(0 0% 8%)", primary: "hsl(15 62% 56%)", border: "hsl(0 0% 100% / 0.12)" },
  },
  {
    id: "mac",
    mode: "light",
    paletteClass: "theme-mac",
    preview: { bg: "hsl(240 6% 96%)", card: "hsl(0 0% 100%)", primary: "hsl(211 100% 45%)", border: "hsl(240 6% 10% / 0.14)" },
  },
  {
    id: "warm-paper",
    mode: "light",
    paletteClass: null,
    preview: { bg: "hsl(48 33% 97%)", card: "hsl(0 0% 100%)", primary: "hsl(15 58% 50%)", border: "hsl(40 8% 17% / 0.10)" },
  },
];

const PALETTE_CLASSES = THEMES.map((t) => t.paletteClass).filter((c): c is string => c !== null);

export function getThemeDef(id: ThemeId): ThemeDef | null {
  if (id === "system") return null;
  return THEMES.find((t) => t.id === id) ?? null;
}

/** 各 mode 的默认主题（2026-08-24 起）：浅色=Mac、深色=Claude 深色（charcoal）。
    首次访问无 stored、system 态解析、快捷翻转三处共用这一对。 */
export function defaultThemeFor(mode: ThemeMode): "mac" | "charcoal" {
  return mode === "light" ? "mac" : "charcoal";
}

/** 快捷翻转的目标：对侧 mode 的默认主题（dark→mac / light→charcoal）。
    palette 主题（midnight/graphite/warm-paper）无对侧变体，一律翻到对侧默认。 */
export function oppositeBaseTheme(mode: ThemeMode): Exclude<ThemeId, "system"> {
  return defaultThemeFor(mode === "dark" ? "light" : "dark");
}

/** 存储值归一：旧 "dark"/"light" → "charcoal"/"warm-paper"；"frost"（改名前旧 id）
    → "mac"；非法值 → null。 */
function normalizeStored(v: string | null): ThemeId | null {
  switch (v) {
    case "dark":
      return "charcoal";
    case "light":
      return "warm-paper";
    case "frost":
      return "mac";
    case "system":
    case "charcoal":
    case "warm-paper":
    case "mac":
    case "midnight":
    case "graphite":
      return v;
    default:
      return null;
  }
}

export function getInitialTheme(): ThemeId {
  if (typeof window === "undefined") return defaultThemeFor("dark");
  const stored = normalizeStored(localStorage.getItem(THEME_KEY));
  if (stored) return stored;
  return defaultThemeFor(window.matchMedia(MQ).matches ? "light" : "dark");
}

/** 解析 theme 的实际渲染 mode：system → 读 prefers-color-scheme；显式主题查 def.mode。 */
export function resolveEffectiveTheme(theme: ThemeId): ThemeMode {
  if (theme === "system") {
    if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
      return window.matchMedia(MQ).matches ? "light" : "dark";
    }
    return "dark";
  }
  return getThemeDef(theme)?.mode ?? "dark";
}

function applyClass(mode: ThemeMode, paletteClass: string | null): void {
  const root = document.documentElement;
  root.classList.remove("dark", "light", ...PALETTE_CLASSES);
  root.classList.add(mode);
  if (paletteClass) root.classList.add(paletteClass);
}

// system 监听单例：applyTheme 切到 system 时注册、切回显式态时清理，避免重复注册 / 泄漏。
let systemMql: MediaQueryList | null = null;
let systemListener: ((e: { matches: boolean }) => void) | null = null;

function detachSystemListener(): void {
  if (systemMql && systemListener) {
    systemMql.removeEventListener("change", systemListener);
  }
  systemMql = null;
  systemListener = null;
}

function attachSystemListener(): void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
  systemMql = window.matchMedia(MQ);
  // system 态 = 跟随系统用「该 mode 的默认主题」（light→mac / dark→charcoal）。
  systemListener = (e) => {
    const mode = e.matches ? "light" : "dark";
    applyClass(mode, getThemeDef(defaultThemeFor(mode))?.paletteClass ?? null);
  };
  systemMql.addEventListener("change", systemListener);
}

export function applyTheme(t: ThemeId): void {
  localStorage.setItem(THEME_KEY, t);
  detachSystemListener();
  if (t === "system") {
    const mode = resolveEffectiveTheme("system");
    applyClass(mode, getThemeDef(defaultThemeFor(mode))?.paletteClass ?? null);
    attachSystemListener();
  } else {
    const def = getThemeDef(t);
    if (def) applyClass(def.mode, def.paletteClass);
  }
}
