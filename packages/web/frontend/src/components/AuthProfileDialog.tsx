// 认证档案 新建/编辑 对话框。镜像 CreateUserDialog.tsx 范式
// (open/onOpenChange/onSaved、busy、reset on close、<form onSubmit>)。
// 字段: 档案名 / login_url / login_type(Select)/ login_flow(Textarea)
// + 初始 credential(role / username / password / totp_secret 折叠)。
// 提交调 createAuthProfile / updateAuthProfile。
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { createAuthProfile, updateAuthProfile } from "@/api/authProfiles";
import { apiErrorMessage } from "@/lib/apiError";
import type { AuthProfile, AuthProfileCredential, VerifyState } from "@/api/types";
import { CredentialRows, type CredentialDraft } from "./auth/CredentialRows";

type LoginType = "form" | "sso" | "api" | "basic";
const LOGIN_TYPES: LoginType[] = ["form", "sso", "api", "basic"];

interface Props {
  ws: string;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
  editing?: AuthProfile | null;
}

export function AuthProfileDialog({ ws, open, onOpenChange, onSaved, editing }: Props) {
  const { t } = useTranslation();
  const [name, setName] = useState(editing?.name ?? "");
  const [loginUrl, setLoginUrl] = useState(editing?.login_url ?? "");
  const [loginType, setLoginType] = useState<LoginType>(editing?.login_type ?? "form");
  const [loginFlow, setLoginFlow] = useState((editing?.login_flow ?? []).join("\n"));
  // 多角色凭据草稿（2026-08-07 #2）：编辑态加载全量 credentials（password/totp 留空=保留原值，
  // 不回显脱敏密文）；新建态一行默认 admin。
  const [drafts, setDrafts] = useState<CredentialDraft[]>(() => {
    if (editing && editing.credentials.length) {
      return editing.credentials.map((c) => ({
        id: c.id, role: c.role, username: c.username, password: "", totpSecret: "",
      }));
    }
    return [{ role: "admin", username: "", password: "", totpSecret: "" }];
  });
  const [busy, setBusy] = useState(false);
  // 系统档案只读防御：editing 正常不会是 system（列表已隐藏 Edit 按钮），此处兜底——
  // 万一外部直接传入 system editing，禁提交，与后端 403 一致。
  const readOnly = editing?.scope === "system";

  function reset() {
    setName(""); setLoginUrl(""); setLoginType("form"); setLoginFlow("");
    setDrafts([{ role: "admin", username: "", password: "", totpSecret: "" }]);
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (readOnly) return;
    if (!name.trim() || !loginUrl.trim() || drafts.some((d) => !d.username.trim())) {
      toast.error(t(editing ? "authProfiles.saveFailed" : "authProfiles.createFailed"));
      return;
    }
    setBusy(true);
    try {
      const flow = loginFlow.split("\n").map((s) => s.trim()).filter(Boolean);
      // 多角色：每条 draft → credential（编辑透传原 id；password/totp 空=保留/不发）。
      const credentials: AuthProfileCredential[] = drafts.map((d) => ({
        id: d.id ?? "",
        role: d.role.trim() || "admin",
        username: d.username.trim(),
        verify_status: { state: "unverified" as VerifyState },
        ...(d.password ? { password: d.password } : {}),
        ...(d.totpSecret.trim() ? { totp_secret: d.totpSecret.trim() } : {}),
      }));
      const body: Partial<AuthProfile> = {
        name: name.trim(),
        login_url: loginUrl.trim(),
        login_type: loginType,
        ...(flow.length ? { login_flow: flow } : {}),
        credentials,
      };
      if (editing) await updateAuthProfile(ws, editing.id, body);
      else await createAuthProfile(ws, body);
      toast.success(t(editing ? "authProfiles.saved" : "authProfiles.created"));
      reset(); onSaved(); onOpenChange(false);
    } catch (e) {
      toast.error(apiErrorMessage(e, t(editing ? "authProfiles.saveFailed" : "authProfiles.createFailed")));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) reset(); onOpenChange(o); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{editing ? t("authProfiles.edit") : t("authProfiles.create")}</DialogTitle>
        </DialogHeader>
        <form onSubmit={onSubmit} className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="ap-name">{t("authProfiles.name")}</Label>
            <Input id="ap-name" value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ap-url">{t("authProfiles.loginUrl")}</Label>
            <Input id="ap-url" value={loginUrl} onChange={(e) => setLoginUrl(e.target.value)} required />
          </div>
          <div className="space-y-1.5">
            <Label>{t("authProfiles.loginType")}</Label>
            <Select value={loginType} onValueChange={(v) => setLoginType(v as LoginType)}>
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                {LOGIN_TYPES.map((v) => <SelectItem key={v} value={v}>{v}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ap-flow">{t("authProfiles.loginFlow")}</Label>
            <Textarea id="ap-flow" value={loginFlow} onChange={(e) => setLoginFlow(e.target.value)} rows={3} />
          </div>
          <CredentialRows value={drafts} onChange={setDrafts} allowMulti showTotp />
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
            {!readOnly && (
              <Button type="submit" disabled={busy}>{busy ? "…" : t(editing ? "authProfiles.save" : "authProfiles.create")}</Button>
            )}
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
