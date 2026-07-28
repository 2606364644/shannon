export type Theme = "dark" | "light" | "system";
export type EffectiveTheme = "dark" | "light";
export const THEME_KEY = "supernova-theme";

const MQ = "(prefers-color-scheme: light)";

// system 监听单例：applyTheme 切到 system 时注册、切回显式态时清理，避免重复注册 / 泄漏。
let systemMql: MediaQueryList | null = null;
let systemListener: ((e: { matches: boolean }) => void) | null = null;

export function getInitialTheme(): Theme {
  if (typeof window === "undefined") return "dark";
  const stored = localStorage.getItem(THEME_KEY);
  if (stored === "dark" || stored === "light" || stored === "system") return stored;
  return window.matchMedia(MQ).matches ? "light" : "dark";
}

/** 解析 theme 的实际渲染态：system → 读 prefers-color-scheme；显式态原样返回。 */
export function resolveEffectiveTheme(theme: Theme): EffectiveTheme {
  if (theme === "system") {
    if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
      return window.matchMedia(MQ).matches ? "light" : "dark";
    }
    return "dark";
  }
  return theme;
}

function applyClass(t: EffectiveTheme): void {
  const root = document.documentElement;
  root.classList.remove("dark", "light");
  root.classList.add(t);
}

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
  systemListener = (e) => applyClass(e.matches ? "light" : "dark");
  systemMql.addEventListener("change", systemListener);
}

export function applyTheme(t: Theme): void {
  localStorage.setItem(THEME_KEY, t);
  detachSystemListener();
  if (t === "system") {
    applyClass(resolveEffectiveTheme("system"));
    attachSystemListener();
  } else {
    applyClass(t);
  }
}
