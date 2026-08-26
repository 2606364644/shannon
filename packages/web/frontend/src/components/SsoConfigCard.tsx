import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { getSsoAdminConfig, updateSsoAdminConfig, type SsoAdminConfigInput } from "@/api/ssoAdminConfig";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";

/** SSO 运行时配置卡（spec 2026-08-26 §8，设置页 admin section 内）：
 * 总开关 + 4 字段表单，一次 PUT 全量保存，**即时生效无需重启**。
 * 校验失败（400）内联错误提示；updated_by/at 回显最后写入方。 */
export function SsoConfigCard() {
  const { t } = useTranslation();
  const [loaded, setLoaded] = useState<SsoAdminConfigInput | null>(null);
  const [audit, setAudit] = useState<{ updated_at: string; updated_by: string }>({ updated_at: "", updated_by: "" });
  const [draft, setDraft] = useState<SsoAdminConfigInput | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    getSsoAdminConfig()
      .then((c) => {
        if (!alive) return;
        const { updated_at, updated_by, ...rest } = c;
        setLoaded(rest);
        setDraft(rest);
        setAudit({ updated_at, updated_by });
      })
      .catch(() => { /* 加载失败保持骨架屏 */ });
    return () => { alive = false; };
  }, []);

  if (!loaded || !draft) return <Skeleton className="h-40 w-full" data-testid="sso-config-skeleton" />;

  const dirty = JSON.stringify(loaded) !== JSON.stringify(draft);

  function set<K extends keyof SsoAdminConfigInput>(key: K, value: SsoAdminConfigInput[K]) {
    setDraft((d) => (d ? { ...d, [key]: value } : d));
    setError("");
  }

  async function onSave() {
    if (!dirty || saving || !draft) return;
    setSaving(true);
    setError("");
    try {
      const saved = await updateSsoAdminConfig(draft);
      const { updated_at, updated_by, ...rest } = saved;
      setLoaded(rest);
      setDraft(rest);
      setAudit({ updated_at, updated_by });
      toast.success(t("settings.ssoConfig.saved"));
    } catch {
      // 400=校验链拒绝（domain 必填 / passport https / public_base http(s) / ttl 1-168）
      setError(t("settings.ssoConfig.invalidConfig"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card className="space-y-3 p-4" data-testid="sso-config-card">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">{t("settings.ssoConfig.title")}</h2>
          <p className="text-xs text-muted-foreground">{t("settings.ssoConfig.subtitle")}</p>
        </div>
        <label className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
          {t("settings.ssoConfig.enabledLabel")}
          <Switch
            checked={draft.enabled}
            onCheckedChange={(v) => set("enabled", v)}
            data-testid="sso-config-toggle"
          />
        </label>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="sso-auth-domain" className="text-xs">{t("settings.ssoConfig.authDomain")}</Label>
          <Input
            id="sso-auth-domain"
            value={draft.auth_domain}
            onChange={(e) => set("auth_domain", e.target.value.trim())}
            placeholder="codescan.example.com"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="sso-passport-base" className="text-xs">{t("settings.ssoConfig.passportBase")}</Label>
          <Input
            id="sso-passport-base"
            value={draft.passport_base}
            onChange={(e) => set("passport_base", e.target.value.trim())}
            placeholder="https://passport.futuoa.com"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="sso-public-base" className="text-xs">{t("settings.ssoConfig.publicBaseUrl")}</Label>
          <Input
            id="sso-public-base"
            value={draft.public_base_url}
            onChange={(e) => set("public_base_url", e.target.value.trim())}
            placeholder="https://codescan.example.com"
          />
          <p className="text-xs text-muted-foreground">{t("settings.ssoConfig.publicBaseUrlHint")}</p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="sso-ttl" className="text-xs">{t("settings.ssoConfig.ttlHours")}</Label>
          <Input
            id="sso-ttl"
            type="number"
            min={1}
            max={168}
            value={draft.session_ttl_hours}
            onChange={(e) => set("session_ttl_hours", Number(e.target.value) || 0)}
          />
        </div>
      </div>

      {error && (
        <p data-testid="sso-config-error" className="rounded-md border border-destructive/40 bg-destructive/10 px-2 py-1.5 text-xs text-destructive">
          {error}
        </p>
      )}

      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">
          {audit.updated_by
            ? t("settings.ssoConfig.updatedBy", { by: audit.updated_by, at: audit.updated_at })
            : t("settings.ssoConfig.notUpdated")}
        </span>
        <Button variant="cta" size="sm" onClick={() => void onSave()} disabled={!dirty || saving} data-testid="sso-config-save">
          {t("settings.ssoConfig.save")}
        </Button>
      </div>
    </Card>
  );
}
