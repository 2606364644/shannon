import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { apiGet, apiPost } from "@/api/client";

export type AuthUser = { id: number; username: string; role: string };
type AuthState = {
  user: AuthUser | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
};

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiGet<{ user: AuthUser }>("/auth/me", { silent: true })
      .then((r) => setUser(r.user))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  async function login(username: string, password: string) {
    // silent: 凭证错误（401）由 LoginPage 表单提示，不触发 onUnauthorized
    // 整页跳转 /login?expired=1——凭证错 ≠ session 过期，跳转会掩盖错误提示，
    // 用户反复输错即表现为「一直跳转 /login?expired=1」循环。
    await apiGet("/auth/csrf", { silent: true });  // 拿 sn-csrf cookie（写操作 csrf header 由 client 自动注入）
    const r = await apiPost<{ user: AuthUser }>("/auth/login", { username, password }, { silent: true });
    setUser(r.user);
  }

  async function logout() {
    await apiPost("/auth/logout", {});
    setUser(null);
  }

  return <Ctx.Provider value={{ user, loading, login, logout }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth 必须在 AuthProvider 内使用");
  return v;
}
