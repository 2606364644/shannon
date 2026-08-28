import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { applyTheme, defaultThemeFor, getInitialTheme, normalizeStored, type Theme } from "@/lib/theme";
import { apiPut } from "@/api/client";
import { AuthContext } from "@/auth/AuthContext";

interface ThemeContextValue {
  theme: Theme;
  setTheme: (t: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() =>
    typeof window !== "undefined" ? getInitialTheme() : defaultThemeFor("dark")
  );

  // 用原始 AuthContext（可 null）而非 useAuth()：ThemeProvider 在未登录页
  //（LoginPage 等）也要工作，缺 AuthProvider 时按未登录处理而非抛错。
  const auth = useContext(AuthContext);

  // mount + 每次 theme 变化：applyTheme（挂 class + 持久化 + system 注册/清理 matchMedia 监听）。
  // 放 effect 而非 setTheme 内，确保 stored="system" 首次挂载时也注册监听，否则系统偏好变化无人响应。
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // 远端校准（2026-08-28 per-user 主题）：后端为 SSOT，user.theme 到达且与当前不同
  // → 应用之。仅 setState——上面 [theme] effect 的 applyTheme 会顺带把校准值写进
  // localStorage（首帧缓存同步），但不会触发 PUT（回写只在 setTheme 暴露函数里），
  // 校准→回写→再校准的环不存在。null/非法值不校准（脏值防御，回落本地）。
  const remoteTheme = normalizeStored(auth?.user?.theme ?? null);
  useEffect(() => {
    if (!remoteTheme) return;
    setThemeState((cur) => (cur === remoteTheme ? cur : remoteTheme));
  }, [remoteTheme]);

  // 本地设置：照旧 applyTheme（localStorage 持久化）；登录态追加 fire-and-forget
  // 回写后端（跨设备一致）。失败静默——localStorage 已兜底，下次 setTheme 重试。
  const loggedIn = auth?.user != null;
  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
    if (loggedIn) {
      apiPut("/users/me/theme", { theme: t }, { silent: true }).catch(() => {});
    }
  }, [loggedIn]);

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
