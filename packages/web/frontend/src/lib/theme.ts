export type Theme = "dark" | "light";
export const THEME_KEY = "supernova-theme";

export function getInitialTheme(): Theme {
  if (typeof window === "undefined") return "dark";
  const stored = localStorage.getItem(THEME_KEY);
  if (stored === "dark" || stored === "light") return stored;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function applyTheme(t: Theme): void {
  const root = document.documentElement;
  root.classList.remove("dark", "light");
  root.classList.add(t);
  localStorage.setItem(THEME_KEY, t);
}
