import { useEffect, useState, useCallback, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Sun, Moon, Monitor, Check, RotateCcw, Lock } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ErrorState";
import { PageHeader } from "@/components/PageHeader";
import { useSystemStatus } from "@/api/systemStatus";
import { apiGet } from "@/api/client";
import { useTheme } from "@/lib/theme-context";
import type { Theme } from "@/lib/theme";
import { useAuth } from "@/auth/AuthContext";
import { useBrand, useBrandEditor } from "@/brand/BrandContext";
import { BrandMark } from "@/components/layout/BrandMark";
import { ChangePasswordDialog } from "@/components/ChangePasswordDialog";

const MAX_BRAND = 32;

/** 分区：coral 竖条 + eyebrow 小标题（uppercase tracking-wider）拉层次。 */
function Section({ eyebrow, children }: { eyebrow: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="h-3.5 w-1 rounded-full bg-primary" aria-hidden />
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{eyebrow}</h2>
      </div>
      {children}
    </section>
  );
}

/** 主题三段选择器：浅色 / 深色 / 跟随系统。active 靠 hairline 浮起（bg-background + shadow-sm），不靠大亮度差。 */
function ThemeSegmented() {
  const { t } = useTranslation();
  const { theme, setTheme } = useTheme();
  const opts: { value: Theme; label: string; icon: ReactNode }[] = [
    { value: "light", label: t("settings.themeLight"), icon: <Sun className="size-3.5" /> },
    { value: "dark", label: t("settings.themeDark"), icon: <Moon className="size-3.5" /> },
    { value: "system", label: t("settings.themeSystem"), icon: <Monitor className="size-3.5" /> },
  ];
  return (
    <div
      role="group"
      aria-label={t("settings.themeTitle")}
      className="inline-flex items-center gap-1 rounded-lg border border-border bg-muted/40 p-1"
    >
      {opts.map((o) => {
        const active = theme === o.value;
        return (
          <Button
            key={o.value}
            type="button"
            variant="ghost"
            size="sm"
            aria-pressed={active}
            aria-label={o.label}
            onClick={() => setTheme(o.value)}
            className={`gap-1.5 font-medium ${active ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
          >
            {o.icon}
            <span>{o.label}</span>
          </Button>
        );
      })}
    </div>
  );
}

/**
 * 品牌名编辑卡 —— signature 元素：live wordmark 预览。
 * 输入即时在「预览」框里渲染 BrandMark + 名称（所见即左上角所得）；
 * Save 才落盘 + 更新全局 TopBar / 浏览器标签 title。
 * admin 可改；非 admin 只读 + 锁标。
 */
function BrandingCard() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const brand = useBrand();
  const { setBrand, saving } = useBrandEditor();
  const isAdmin = user?.role === "admin";

  // 输入框本地态：初始=当前生效名；改了未保存时 dirty。
  const [draft, setDraft] = useState(brand);
  const [savedFlash, setSavedFlash] = useState(false);

  // 全局品牌变更(本卡 save/reset 落盘后、或外部刷新)时同步 draft。
  // system-status 无轮询,brand 变化只来自本卡的 setBrand 调用,故始终跟随安全。
  useEffect(() => {
    setDraft(brand);
  }, [brand]);

  const trimmed = draft.trim();
  const dirty = trimmed !== brand.trim();
  const overLimit = trimmed.length > MAX_BRAND;
  const canSave = isAdmin && dirty && trimmed.length > 0 && !overLimit && !saving;

  // 是否存在运行时覆盖(branding.json)。GET /api/branding 拿这个信号:
  // reset 的意义是「清除我的自定义名 → 回部署默认」,只有覆盖存在时才有意义。
  const [hasOverride, setHasOverride] = useState<boolean | null>(null);
  const refreshOverride = useCallback(async () => {
    try {
      const r = await apiGet<{ brand_name: string | null }>("/branding", { silent: true });
      setHasOverride(r.brand_name != null);
    } catch {
      setHasOverride(null);
    }
  }, []);
  useEffect(() => {
    if (isAdmin) void refreshOverride();
  }, [isAdmin, refreshOverride]);

  async function onSave() {
    if (!canSave) return;
    const ok = await setBrand(trimmed);
    if (ok) {
      setHasOverride(true); // 已设置覆盖
      toast.success(t("branding.saved"));
      setSavedFlash(true);
      setTimeout(() => setSavedFlash(false), 1800);
    } else {
      toast.error(t("branding.errors.saveFailed"));
    }
  }

  async function onReset() {
    // 清除覆盖 → 回落 env/default;draft 由 useEffect 跟随新 brand 同步。
    const ok = await setBrand(null);
    if (ok) {
      setHasOverride(false);
      toast.success(t("branding.saved"));
    } else {
      toast.error(t("branding.errors.saveFailed"));
    }
  }

  // 预览优先显示 draft（所见即所得），空时回落当前生效名。
  const previewName = trimmed || brand;

  return (
    <Card>
      <CardHeader className="p-4 pb-3">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="font-semibold tracking-tight text-base">{t("branding.title")}</CardTitle>
          {!isAdmin && (
            <Badge variant="outline" className="gap-1 border-border text-muted-foreground">
              <Lock className="size-3" /> {t("branding.adminOnly")}
            </Badge>
          )}
        </div>
        <p className="text-sm text-muted-foreground">{t("branding.desc")}</p>
      </CardHeader>
      <CardContent className="p-4 pt-0">
        <div className="grid gap-3 md:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)] md:items-end">
          {/* ▍live wordmark 预览：复刻 TopBar 字标观感，输入即时反映 */}
          <div className="space-y-1.5">
            <span className="text-xs font-medium text-muted-foreground">{t("branding.preview")}</span>
            <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2">
              <BrandMark className="h-5 w-5 text-foreground" />
              <span className="text-base font-semibold tracking-tight">{previewName}</span>
            </div>
          </div>

          {isAdmin ? (
            <div className="space-y-1.5">
              <div className="flex items-baseline justify-between">
                <Label htmlFor="brand-name-input" className="text-sm">{t("branding.fieldLabel")}</Label>
                <span className={`text-xs tabular-nums ${overLimit ? "text-red" : "text-muted-foreground"}`}>
                  {t("branding.charCount", { count: trimmed.length, max: MAX_BRAND })}
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  id="brand-name-input"
                  value={draft}
                  maxLength={MAX_BRAND + 8}
                  placeholder={t("branding.placeholder")}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && canSave) onSave();
                  }}
                  aria-invalid={overLimit}
                  className="min-w-0 max-w-xs flex-1"
                />
                <Button
                  type="button"
                  size="sm"
                  onClick={onSave}
                  disabled={!canSave}
                  className="gap-1.5"
                  data-testid="brand-save"
                >
                  {savedFlash ? <Check className="size-3.5" /> : null}
                  {t("branding.save")}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={onReset}
                  disabled={saving || !hasOverride}
                  className="gap-1.5 text-muted-foreground"
                  title={t("branding.reset")}
                >
                  <RotateCcw className="size-3.5" />
                  {t("branding.reset")}
                </Button>
              </div>
            </div>
          ) : (
            <div className="space-y-1.5">
              <Label className="text-sm text-muted-foreground">{t("branding.readOnlyHint")}</Label>
              <div className="flex items-center gap-2 rounded-md border border-border bg-muted/20 px-3 py-2">
                <span className="font-medium">{brand}</span>
              </div>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export function SettingsPage() {
  const { t } = useTranslation();
  const { data, loading, error, refresh } = useSystemStatus();
  const { user, refreshUser } = useAuth();
  const [cpOpen, setCpOpen] = useState(false);

  return (
    <div className="space-y-5">
      <PageHeader title={t("settings.title")} subtitle={t("settings.subtitle")} />

      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1.12fr)_minmax(0,0.88fr)]">
        <div className="space-y-5">
          {/* ▍品牌 */}
          <Section eyebrow={t("settings.section.branding")}>
            <BrandingCard />
          </Section>

          {/* ▍个人化 */}
          <Section eyebrow={t("settings.section.personalization")}>
            <div className="grid gap-3 sm:grid-cols-2">
              <Card>
                <CardHeader className="p-4 pb-3">
                  <CardTitle className="font-semibold tracking-tight text-base">{t("settings.themeTitle")}</CardTitle>
                </CardHeader>
                <CardContent className="p-4 pt-0">
                  <ThemeSegmented />
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="p-4 pb-3">
                  <CardTitle className="font-semibold tracking-tight text-base">{t("settings.accountSecurityTitle")}</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-wrap items-center gap-3 p-4 pt-0 text-sm">
                  <span className="text-muted-foreground">{t("settings.accountSecurityHint")}</span>
                  {user?.must_change_password && (
                    <Badge variant="outline" className="border-amber/50 text-amber">{t("auth.mustChange.badgeShort")}</Badge>
                  )}
                  <Button variant="outline" size="sm" onClick={() => setCpOpen(true)} className="ml-auto">
                    {t("settings.changePasswordBtn")}
                  </Button>
                </CardContent>
              </Card>
            </div>
          </Section>
        </div>

        {/* ▍系统 */}
        <Section eyebrow={t("settings.section.system")}>
          <Card>
            <CardHeader className="p-4 pb-3">
              <CardTitle className="font-semibold tracking-tight text-base">{t("settings.statusTitle")}</CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              {loading && <Skeleton className="h-20 w-full" />}
              {error && <ErrorState message={t("settings.errors.loadFailed", { error })} onRetry={refresh} />}
              {data && (
                <dl className="grid grid-cols-[minmax(108px,auto)_1fr] gap-y-1.5 font-mono text-sm">
                  <dt className="text-muted-foreground">{t("settings.fields.aiProvider")}</dt>
                  <dd>{data.ai_provider}</dd>
                  <dt className="text-muted-foreground">{t("settings.fields.browserEngine")}</dt>
                  <dd>{data.browser_engine}</dd>
                  <dt className="text-muted-foreground">Temporal</dt>
                  <dd className="flex items-center gap-2">
                    {data.temporal.host}
                    <Badge
                      variant="outline"
                      className={data.temporal.last_status === "connected" ? "border-green/40 text-green" : "border-red/40 text-red"}
                    >
                      {data.temporal.last_status}
                    </Badge>
                  </dd>
                  <dt className="text-muted-foreground">{t("settings.fields.gitBinary")}</dt>
                  <dd>{data.git.binary_available ? t("settings.gitBinary.installed") : t("settings.gitBinary.missing")}</dd>
                  <dt className="text-muted-foreground">{t("settings.fields.gitCredentials")}</dt>
                  <dd className="break-words">{data.git.credentials_configured ? t("settings.gitCredentials.configured") : t("settings.gitCredentials.notConfigured")}</dd>
                  <dt className="text-muted-foreground">{t("settings.fields.version")}</dt>
                  <dd>{data.version}</dd>
                </dl>
              )}
              {data && (
                <p className="mt-3 border-t border-border pt-2 text-xs text-muted-foreground">{t("settings.perWsHint")}</p>
              )}
            </CardContent>
          </Card>
        </Section>
      </div>

      <ChangePasswordDialog open={cpOpen} onOpenChange={setCpOpen} onChanged={refreshUser} />
    </div>
  );
}
