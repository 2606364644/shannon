import { useState } from "react";
import type { FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ThemeToggle } from "@/components/layout/ThemeToggle";

const SERIF = { fontFamily: "var(--font-serif), Georgia, serif" } as const;
const GRADIENT = "linear-gradient(155deg, #2E2520 0%, #4A2E22 55%, #6B3A26 100%)";

export default function LoginPage() {
  const { t } = useTranslation();
  const { user, login } = useAuth();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to="/" replace />;
  const expired = params.get("expired") === "1";
  const next = params.get("next") || "/";

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      await login(username, password);
      nav(next, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? t("auth.login.invalid") : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen">
      {/* 主题切换（登录页不在 AppShell 内，独立浮动；复用全局 lib/theme.ts 机制） */}
      <div className="fixed top-4 right-4 z-10">
        <ThemeToggle />
      </div>
      {/* 左品牌区 */}
      <div
        className="hidden flex-col items-center justify-center gap-3 text-white md:flex md:w-[45%]"
        style={{ background: GRADIENT }}
      >
        <div
          className="flex h-14 w-14 items-center justify-center rounded-full bg-primary text-2xl"
          style={{ boxShadow: "0 0 0 8px hsl(var(--c-orange) / 0.18)" }}
        >
          ✦
        </div>
        <div className="text-2xl font-semibold tracking-tight" style={SERIF}>Supernova</div>
        <div className="text-sm opacity-70">{t("auth.tagline")}</div>
      </div>
      {/* 右表单 */}
      <div className="flex flex-1 items-center justify-center bg-background p-6">
        <form onSubmit={onSubmit} className="w-full max-w-sm space-y-4">
          <div className="space-y-1">
            <h1 className="text-2xl font-semibold text-foreground" style={SERIF}>
              {t("auth.login.welcome")}
            </h1>
            <p className="text-sm text-muted-foreground">{t("auth.login.sub")}</p>
          </div>
          {expired && <p className="text-sm text-destructive">{t("auth.sessionExpired")}</p>}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <div className="space-y-2">
            <Label htmlFor="u">{t("auth.login.username")}</Label>
            <Input id="u" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
          </div>
          <div className="space-y-2">
            <Label htmlFor="p">{t("auth.login.password")}</Label>
            <Input id="p" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
          </div>
          <Button type="submit" className="w-full" disabled={busy}>
            {busy ? "…" : t("auth.login.submit")}
          </Button>
          <div className="rounded-md border border-border/60 bg-muted/40 px-3 py-2 text-center text-xs text-muted-foreground">
            {t("auth.login.defaultHint")}
          </div>
          <p className="text-center text-xs text-muted-foreground">{t("auth.sessionTtlHint")}</p>
        </form>
      </div>
    </div>
  );
}
