import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { applyTheme, defaultThemeFor, getInitialTheme, type Theme } from "@/lib/theme";

interface ThemeContextValue {
  theme: Theme;
  setTheme: (t: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() =>
    typeof window !== "undefined" ? getInitialTheme() : defaultThemeFor("dark")
  );

  // mount + 每次 theme 变化：applyTheme（挂 class + 持久化 + system 注册/清理 matchMedia 监听）。
  // 放 effect 而非 setTheme 内，确保 stored="system" 首次挂载时也注册监听，否则系统偏好变化无人响应。
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  return (
    <ThemeContext.Provider value={{ theme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme 必须在 <ThemeProvider> 内使用");
  return ctx;
}
