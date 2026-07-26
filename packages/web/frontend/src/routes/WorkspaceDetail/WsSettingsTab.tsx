import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { useAuth } from "@/auth/AuthContext";
import { getWsConfig, putWsConfig, type WsProviderFields } from "@/api/wsConfig";
import { getMembers } from "@/api/members";
import type { Member } from "@/api/members";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

const EMPTY: WsProviderFields = {
  ai_provider: null, api_key: null, base_url: null, model: null,
  small_model: null, medium_model: null, large_model: null,
  max_turns: null, adaptive_thinking: null,
};

// 合法 provider 名（与后端 PROVIDER_SETTINGS 键一致）
const PROVIDERS = ["anthropic_api", "openai_compatible", "bedrock", "vertex", "litellm_router"];

// 文本字段（均为 string|null），map 渲染；max_turns(number) 与 adaptive_thinking(bool) 单独处理。
const TEXT_FIELDS: { key: keyof WsProviderFields; labelKey: string }[] = [
  { key: "base_url", labelKey: "baseUrl" },
  { key: "model", labelKey: "model" },
  { key: "small_model", labelKey: "smallModel" },
  { key: "medium_model", labelKey: "mediumModel" },
  { key: "large_model", labelKey: "largeModel" },
];

export default function WsSettingsTab() {
  const { workspace: ws = "" } = useParams<{ workspace: string }>();
  const { t } = useTranslation();
  const { user } = useAuth();
  const [cfg, setCfg] = useState<WsProviderFields>(EMPTY);
  const [members, setMembers] = useState<Member[]>([]);
  const [apiKeyInput, setApiKeyInput] = useState(""); // password 框，空=不改
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    getWsConfig(ws).then((r) => { setCfg(r.provider); setLoaded(true); })
      .catch(() => setLoaded(true));
    getMembers(ws).then((r) => setMembers(r.members)).catch(() => {});
  }, [ws]);

  // workspace 级角色来自 members API（全局 user.role 只有 admin/user）；复用 MemberManagerDialog 模式
  const myRole = user?.role === "admin" ? "admin" : members.find((m) => m.user_id === user?.id)?.role;
  const canEdit = myRole === "admin" || myRole === "manager";

  async function onSave() {
    setBusy(true);
    try {
      await putWsConfig(ws, {
        provider: {
          ...cfg,
          api_key: apiKeyInput || undefined, // 空=不发（后端保原值）
        },
      });
      setApiKeyInput("");
      const fresh = await getWsConfig(ws);
      setCfg(fresh.provider);
      toast.success(t("wsConfig.saved"));
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      const key = status === 403 ? "wsConfig.errors.forbidden"
        : status === 422 ? "wsConfig.errors.invalid"
        : "wsConfig.errors.saveFailed";
      toast.error(t(key));
    } finally {
      setBusy(false);
    }
  }

  if (!loaded) return null;
  return (
    <Card>
      <CardHeader><CardTitle className="font-semibold tracking-tight text-base">{t("wsConfig.title")}</CardTitle></CardHeader>
      <CardContent className="max-w-xl space-y-4">
        <p className="text-sm text-muted-foreground">{t("wsConfig.subtitle")}</p>
        <div className="space-y-2">
          <Label>{t("wsConfig.fields.aiProvider")}</Label>
          <Select value={cfg.ai_provider ?? "__unset__"} disabled={!canEdit}
                  onValueChange={(v) => setCfg({ ...cfg, ai_provider: v === "__unset__" ? null : v })}>
            <SelectTrigger><SelectValue placeholder={t("wsConfig.fallbackHint")} /></SelectTrigger>
            <SelectContent>
              <SelectItem value="__unset__">{t("wsConfig.fallbackHint")}</SelectItem>
              {PROVIDERS.map((p) => <SelectItem key={p} value={p}>{p}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-2">
          <Label>{t("wsConfig.fields.apiKey")}</Label>
          <Input type="password" value={apiKeyInput} disabled={!canEdit}
                 placeholder={cfg.api_key ? t("wsConfig.apiKey.configured") : t("wsConfig.apiKey.notConfigured")}
                 onChange={(e) => setApiKeyInput(e.target.value)} />
        </div>
        {TEXT_FIELDS.map(({ key, labelKey }) => (
          <div className="space-y-2" key={key}>
            <Label>{t(`wsConfig.fields.${labelKey}`)}</Label>
            <Input value={(cfg[key] as string) ?? ""} disabled={!canEdit}
                   onChange={(e) => setCfg({ ...cfg, [key]: e.target.value || null })} />
          </div>
        ))}
        <div className="space-y-2">
          <Label>{t("wsConfig.fields.maxTurns")}</Label>
          <Input type="number" value={cfg.max_turns ?? ""} disabled={!canEdit}
                 onChange={(e) => setCfg({ ...cfg, max_turns: e.target.value ? Number(e.target.value) : null })} />
        </div>
        <div className="flex items-center gap-2">
          <Switch checked={cfg.adaptive_thinking ?? false} disabled={!canEdit}
                  onCheckedChange={(v) => setCfg({ ...cfg, adaptive_thinking: v })} />
          <Label>{t("wsConfig.fields.adaptiveThinking")}</Label>
        </div>
        {canEdit && (
          <Button onClick={onSave} disabled={busy}>{t("wsConfig.save")}</Button>
        )}
      </CardContent>
    </Card>
  );
}
