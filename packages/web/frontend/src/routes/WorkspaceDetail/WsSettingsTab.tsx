import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { useAuth } from "@/auth/AuthContext";
import { getWsConfig, putWsConfig, type WsConfigWarnings } from "@/api/wsConfig";
import { getMembers } from "@/api/members";
import type { Member } from "@/api/members";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

const PLACEHOLDER = [
  "SUPERNOVA_AI_PROVIDER=openai_compatible",
  "SUPERNOVA_OPENAI_API_KEY=填入你的 API key",
  "SUPERNOVA_OPENAI_BASE_URL=https://llm-proxy.futuoa.com/v1",
  "SUPERNOVA_OPENAI_LARGE_MODEL=glm-5.2-coder",
  "SUPERNOVA_OPENAI_MEDIUM_MODEL=glm-5.2-coder",
  "SUPERNOVA_OPENAI_SMALL_MODEL=glm-5.2-coder",
].join("\n");

export default function WsSettingsTab() {
  const { workspace: ws = "" } = useParams<{ workspace: string }>();
  const { t } = useTranslation();
  const { user } = useAuth();
  const [envText, setEnvText] = useState("");
  const [warnings, setWarnings] = useState<WsConfigWarnings | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    getWsConfig(ws).then((r) => { setEnvText(r.env_text); setLoaded(true); })
      .catch(() => setLoaded(true));
    getMembers(ws).then((r) => setMembers(r.members)).catch(() => {});
  }, [ws]);

  // workspace 级角色来自 members API（全局 user.role 只有 admin/user）；复用 MemberManagerDialog 模式
  const myRole = user?.role === "admin" ? "admin" : members.find((m) => m.user_id === user?.id)?.role;
  const canEdit = myRole === "admin" || myRole === "manager";

  async function onSave() {
    setBusy(true);
    setWarnings(null);
    try {
      const r = await putWsConfig(ws, envText);
      // 保存后用后端渲染的 env 文本重置（凭据回填掩码、清空字段落实）
      const fresh = await getWsConfig(ws);
      setEnvText(fresh.env_text);
      if (r.warnings && (r.warnings.ineffective.length || r.warnings.unknown.length)) {
        setWarnings(r.warnings);
      }
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
          <Label htmlFor="ws-env-text">{t("wsConfig.envText")}</Label>
          <Textarea
            id="ws-env-text"
            aria-label={t("wsConfig.envText")}
            className="font-mono text-sm min-h-[280px]"
            value={envText}
            disabled={!canEdit}
            placeholder={PLACEHOLDER}
            onChange={(e) => setEnvText(e.target.value)}
          />
        </div>
        {warnings && (
          <div className="space-y-1 text-sm text-amber-600 dark:text-amber-500">
            {warnings.ineffective.length > 0 && (
              <p>{t("wsConfig.warnings.ineffective")}: {warnings.ineffective.join(", ")}</p>
            )}
            {warnings.unknown.length > 0 && (
              <p>{t("wsConfig.warnings.unknown")}: {warnings.unknown.join(", ")}</p>
            )}
          </div>
        )}
        {canEdit && (
          <Button onClick={onSave} disabled={busy}>{t("wsConfig.save")}</Button>
        )}
      </CardContent>
    </Card>
  );
}
