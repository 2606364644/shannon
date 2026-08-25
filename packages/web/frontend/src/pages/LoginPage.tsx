import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { useBrand } from "@/brand/BrandContext";
import { ApiError, getSsoConfig } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ThemeToggle } from "@/components/layout/ThemeToggle";

const SERIF = { fontFamily: "var(--font-serif), Georgia, serif" } as const;
const GRADIENT = "linear-gradient(155deg, #2E2520 0%, #4A2E22 55%, #6B3A26 100%)";

export default function LoginPage() {
  const { t } = useTranslation();
  const { user, login } = useAuth();
  const brand = useBrand();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [ssoEnabled, setSsoEnabled] = useState(false);
  // SSO 回跳错误码（/api/auth/sso/callback 失败重定向回 /login?sso_error=...）
  const ssoError = params.get("sso_error");

  useEffect(() => {
    // silent+catch：config 端点不可达按 disabled 处理（按钮不渲染，不阻塞账密登录）
    getSsoConfig().then((c) => setSsoEnabled(!!c.enabled)).catch(() => setSsoEnabled(false));
  }, []);

  if (user) return <Navigate to="/" replace />;
  const expired = params.get("expired") === "1";
  const next = params.get("next") || "/";

  function onSsoLogin() {
    // 整页跳后端 302 到 OA 授权页；next 编码回跳地址，SSO 回调成功后送回前端
    window.location.assign(`/api/auth/sso/login?next=${encodeURIComponent(next)}`);
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      await login(username, password);
      nav(next, { replace: true });
    } catch (err) {
      // 401=凭证错；429=BruteGuard 锁定（5 次失败锁 5 分钟，正确密码也被拒），
      // 须明确提示"稍后再试"而非笼统 Login failed——否则用户以为密码错继续试。
      const status = err instanceof ApiError ? err.status : 0;
      setError(
        status === 401 ? t("auth.login.invalid")
          : status === 429 ? t("auth.login.locked")
          : "Login failed",
      );
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
        <div className="text-2xl font-semibold tracking-tight" style={SERIF}>{brand}</div>
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
          {ssoError && (
            <p role="alert" className="text-sm text-destructive">
              {ssoError === "not_whitelisted" ? t("auth.login.ssoNotWhitelisted") : t("auth.login.ssoFailed")}
            </p>
          )}
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
          {ssoEnabled && (
            <>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className="h-px flex-1 bg-border" />
                {t("auth.login.or")}
                <span className="h-px flex-1 bg-border" />
              </div>
              <Button type="button" variant="outline" className="w-full" onClick={onSsoLogin} data-testid="sso-login-btn">
                {t("auth.login.ssoButton")}
              </Button>
            </>
          )}
          <div className="rounded-md border border-border/60 bg-muted/40 px-3 py-2 text-center text-xs text-muted-foreground">
            {t("auth.login.defaultHint")}
          </div>
          <p className="text-center text-xs text-muted-foreground">{t("auth.sessionTtlHint")}</p>
        </form>
      </div>
    </div>
  );
}
