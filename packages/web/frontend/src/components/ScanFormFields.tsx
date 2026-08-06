import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { RepoCombobox } from "./RepoCombobox";
import { AddRepoDialog } from "./AddRepoDialog";
import { CloneProgress } from "./CloneProgress";
import { listRepos, listScans } from "@/api/client";
import { listAuthProfiles, createAuthProfile } from "@/api/authProfiles";
import type { Repo, ScanSummary, Workspace, AuthProfile, AuthProfileCredential, VerifyState } from "@/api/types";
import type { FormState, AuthFormState } from "../pages/ScanNewPage";
import { useAuth } from "@/auth/AuthContext";
import { apiErrorMessage } from "@/lib/apiError";
import { toast } from "sonner";
import { AlertCircle, Info } from "lucide-react";

/** AuthFormState（inline 临时填写）-> AuthProfile 创建 body（保存为档案）。
 *  字段一一映射，完整保留 totp_secret + email_login（不丢字段）；credential id 占位（后端分配），
 *  verify_status=unverified（新建档案未经验证）。name + role 由调用方补（inline 无此概念）。 */
function authToProfileBody(auth: AuthFormState, name: string, role: string): Partial<AuthProfile> {
  const cred: AuthProfileCredential = {
    id: "",
    role,
    username: auth.username.trim(),
    verify_status: { state: "unverified" as VerifyState },
    ...(auth.password ? { password: auth.password } : {}),
    ...(auth.totpSecret.trim() ? { totp_secret: auth.totpSecret.trim() } : {}),
    ...(auth.emailLoginEnabled ? {
      email_login: {
        address: auth.emailAddress.trim(),
        ...(auth.emailPassword ? { password: auth.emailPassword } : {}),
        ...(auth.emailTotp.trim() ? { totp_secret: auth.emailTotp.trim() } : {}),
      },
    } : {}),
  };
  const flow = auth.loginFlow.split("\n").map((s) => s.trim()).filter(Boolean);
  return {
    name: name.trim(),
    login_url: auth.loginUrl.trim(),
    login_type: auth.loginType,
    ...(flow.length ? { login_flow: flow } : {}),
    credentials: [cred],
  };
}

/** 验证状态 → 徽章样式 + 图标（与 CredentialRow 同色系：success=绿✓ / failed=红✗ / unverified=黄●）。 */
function verifyBadge(st: VerifyState): { cls: string; icon: string } {
  return st === "success" ? { cls: "border-green/40 text-green", icon: "✓" }
    : st === "failed" ? { cls: "border-red/40 text-red", icon: "✗" }
    : { cls: "border-yellow/40 text-yellow", icon: "●" };
}
/** 档案整体状态：任一角色 success→可用；否则任一 failed→不可用；否则未验证。 */
function overallState(creds: AuthProfileCredential[]): VerifyState {
  if (creds.some((c) => c.verify_status?.state === "success")) return "success";
  if (creds.some((c) => c.verify_status?.state === "failed")) return "failed";
  return "unverified";
}
/** login_url → host（档案卡显示用；解析失败回落原值）。 */
function hostOf(url: string): string {
  try { return new URL(url).host; } catch { return url; }
}

interface Props {
  type: "whitebox" | "blackbox";
  f: FormState;
  set: (patch: Partial<FormState>) => void;
  sourceErr: string | null;
  /** 黑盒 reuse 模式下未选白盒扫描的提示（仅 blackbox + reuse 模式传入）。 */
  reuseErr: string | null;
  urlErr: string | null;
  /** 黑盒登录配置校验错误（仅 blackbox 传入）。 */
  authErr: string | null;
  /** P2: 选定的目标 workspace——驱动 listRepos(ws) / listScans(ws) 与子组件 ws 参数 */
  workspace: string;
  /** P2: 用户可见的 ws 列表（P1 后端已过滤）——供下拉选项 */
  wsList: Workspace[];
  /** P2: ws 下拉变更回调 */
  onWorkspaceChange: (ws: string) => void;
  /** ws 列表加载中（防首帧 [] 误判为空态闪现提示） */
  wsLoading: boolean;
  /** 重跑预填的黑盒复用 scan_id（同 ws）；首帧保留预填值，不被 ws-change 清空 / 默认选最新覆盖。 */
  presetReuseScanId?: string;
}

/** 分组小标题：coral 竖条 eyebrow（复用 settings Section 的视觉语言，适配中文卡内分组——
 *  去 uppercase/tracking-wider，仅保留 coral 竖条 + 小号 semibold 标签拉层次）。 */
function GroupLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="h-3 w-[3px] rounded-full bg-primary" aria-hidden />
      <span className="text-[11px] font-semibold text-muted-foreground">{children}</span>
    </div>
  );
}

/** 步骤分组容器：圆角 + secondary 背景 + 边框（仅白盒用；黑盒已改为轻分区） */
function StepGroup({ step, title, tag, tagClass, className, children }: {
  step: number;
  title: string;
  tag?: string;
  tagClass?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={`rounded-lg border border-border bg-secondary p-3.5 space-y-2.5 ${className ?? ""}`}>
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center justify-center w-[22px] h-[22px] rounded-full bg-primary text-primary-foreground text-[11px] font-semibold flex-shrink-0">
          {step}
        </span>
        <span className="text-[13px] font-semibold">{title}</span>
        {tag && (
          <span className={`ml-auto inline-flex items-center rounded-full px-2 py-0.5 text-[10.5px] font-semibold ${tagClass ?? ""}`}>
            {tag}
          </span>
        )}
      </div>
      {children}
    </div>
  );
}

/** 黑盒登录配置区。重排后（2026-08-06）结构：
 *    - 顶部 Switch「需要登录」（enabled=false 时仅显开关 + 提示）。
 *    - enabled=true 时显来源 segmented（临时填写 / 使用档案）+ 对方面板：
 *        inline → InlineAuthFields（双列：登录入口 / 凭据）。
 *        profile → ProfilePicker（双列：档案卡列表 / 选中档案详情+角色）。
 *  外层（ScanFormFields 黑盒分支）用 authExpanded 控制整块折叠/展开，默认折叠成一行。 */
function AuthControls({ auth, setAuth, authErr, workspace, refreshSignal, onProfileSaved }: {
  auth: AuthFormState;
  setAuth: (patch: Partial<AuthFormState>) => void;
  authErr: string | null;
  workspace: string;
  refreshSignal: number;
  onProfileSaved: (profile: AuthProfile) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <Label className="text-xs font-medium">{t("scan.auth.enableLabel")}</Label>
        <Switch checked={auth.enabled} onCheckedChange={(v) => setAuth({ enabled: v })} />
      </div>

      {!auth.enabled ? (
        <div className="text-xs text-muted-foreground">{t("scan.auth.enableHint")}</div>
      ) : (
        <>
          {/* 来源 segmented（替代旧 Select）：临时填写 / 使用档案 */}
          <div className="inline-flex items-center gap-1 rounded-lg border border-border bg-muted/40 p-1">
            {(["inline", "profile"] as const).map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setAuth({ source: s })}
                aria-pressed={auth.source === s}
                className={`inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-md text-xs font-medium transition-colors ${
                  auth.source === s ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {t(`authProfiles.source${s === "inline" ? "Inline" : "Profile"}`)}
              </button>
            ))}
          </div>

          {auth.source === "profile" ? (
            <ProfilePicker auth={auth} setAuth={setAuth} workspace={workspace} refreshSignal={refreshSignal} />
          ) : (
            <InlineAuthFields auth={auth} setAuth={setAuth} authErr={authErr} ws={workspace} onProfileSaved={onProfileSaved} />
          )}

          {/* profile 模式校验错误贴 ProfilePicker 显；inline 模式错误交 InlineAuthFields 显，避免左右重复。 */}
          {auth.source === "profile" && authErr && <div className="text-destructive text-xs">{authErr}</div>}
        </>
      )}

      <div className="flex items-start gap-1.5 text-[11px] text-muted-foreground leading-relaxed">
        <Info className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
        <span>{t("scan.auth.infoNote")}</span>
      </div>
    </div>
  );
}

/** inline 模式「保存为档案」区--与临时填写字段同处右列底部，非独立对话框（用户意图：保存能力与
 *  填写区在一起，既能直接运行也能保存）。展开后填档案名 + 角色（默认 admin），提交调
 *  createAuthProfile，成功后 onSaved(新档案) 回调让父级切 profile 模式并选中。
 *  - 复用 authToProfileBody（完整保留 totp_secret + email_login，不丢字段）。
 *  - 保存前置校验：loginUrl + username 必填（profile 必备），缺则 toast 拦截不发请求。
 *  - ws 内 name 唯一（后端 422）-> 失败 toast 提示「档案名已存在」。 */
function SaveAsProfileInline({ auth, ws, onSaved }: {
  auth: AuthFormState;
  ws: string;
  onSaved: (profile: AuthProfile) => void;
}) {
  const { t } = useTranslation();
  const [show, setShow] = useState(false);
  const [name, setName] = useState("");
  const [role, setRole] = useState("admin");
  const [busy, setBusy] = useState(false);

  async function onSave(e: React.FormEvent) {
    e.preventDefault();
    if (!auth.loginUrl.trim() || !auth.username.trim()) {
      toast.error(t("scan.auth.saveNeedFields"));
      return;
    }
    if (!name.trim()) return;
    setBusy(true);
    try {
      const profile = await createAuthProfile(ws, authToProfileBody(auth, name, role));
      toast.success(t("scan.auth.saveSuccess"));
      setShow(false);
      setName("");
      onSaved(profile);
    } catch (err) {
      toast.error(apiErrorMessage(err, t("authProfiles.createFailed")));
    } finally {
      setBusy(false);
    }
  }

  // 保存前置：loginUrl + username 必填（档案 credential 必备）。未填则展开按钮禁用 + 常驻提示，
  // 避免用户展开填完档案名才在提交时被告知缺字段（早反馈）。onSave 保留同校验作双保险。
  const canSave = !!auth.loginUrl.trim() && !!auth.username.trim();

  if (!show) {
    return (
      <div className="space-y-1">
        <Button type="button" variant="ghost" size="sm" onClick={() => setShow(true)} disabled={!canSave}>
          {t("scan.auth.saveAsProfile")}
        </Button>
        {!canSave && (
          <div className="text-[11px] text-muted-foreground">{t("scan.auth.saveNeedFields")}</div>
        )}
      </div>
    );
  }
  return (
    <form onSubmit={onSave} className="space-y-2 rounded-lg border border-border bg-card p-2.5">
      <GroupLabel>{t("scan.auth.saveAsProfile")}</GroupLabel>
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <Label className="text-[11px] text-muted-foreground">{t("authProfiles.name")}</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("scan.auth.profileNamePlaceholder")} required autoFocus />
        </div>
        <div className="space-y-1">
          <Label className="text-[11px] text-muted-foreground">{t("authProfiles.role")}</Label>
          <Input value={role} onChange={(e) => setRole(e.target.value)} />
        </div>
      </div>
      <div className="text-[11px] text-muted-foreground leading-relaxed">{t("scan.auth.saveAsProfileHint")}</div>
      <div className="flex items-center gap-2">
        <Button type="submit" size="sm" disabled={busy}>{busy ? "…" : t("scan.auth.saveAsProfile")}</Button>
        <Button type="button" variant="ghost" size="sm" onClick={() => setShow(false)}>{t("common.cancel")}</Button>
      </div>
    </form>
  );
}

/** inline 模式字段块——双列布局（2026-08-06 重排）：
 *    左列「登录入口」：登录方式 button-group + 登录地址。
 *    右列「凭据」：用户名 / 密码 / TOTP / 邮箱登录（checkbox 展开）。
 *  下方全宽：登录步骤（可选）+ 校验错误 + 「保存为认证档案」。
 *  字段经 setAuth 回写 FormState.auth；buildBody 转 ScanAuthentication 发后端。 */
function InlineAuthFields({ auth, setAuth, authErr, ws, onProfileSaved }: {
  auth: AuthFormState;
  setAuth: (patch: Partial<AuthFormState>) => void;
  authErr: string | null;
  ws: string;
  onProfileSaved: (profile: AuthProfile) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 items-start">
        {/* 左：登录入口 */}
        <div className="space-y-3 rounded-lg border border-border bg-secondary p-3">
          <GroupLabel>{t("scan.auth.entryGroup")}</GroupLabel>
          <div className="space-y-1.5">
            <Label className="text-xs font-medium">{t("scan.auth.loginTypeLabel")}</Label>
            <div className="inline-flex flex-wrap items-center gap-1 rounded-lg border border-border bg-muted/40 p-1">
              {(["form", "sso", "api", "basic"] as const).map((v) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setAuth({ loginType: v })}
                  aria-pressed={auth.loginType === v}
                  className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                    auth.loginType === v ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {t(`scan.auth.loginType.${v}`)}
                </button>
              ))}
            </div>
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs font-medium">{t("scan.auth.loginUrlLabel")}</Label>
            <Input
              value={auth.loginUrl}
              onChange={(e) => setAuth({ loginUrl: e.target.value })}
              placeholder="https://example.com/login"
              className="font-mono"
            />
          </div>
        </div>

        {/* 右：凭据 */}
        <div className="space-y-2.5 rounded-lg border border-border bg-secondary p-3">
          <GroupLabel>{t("scan.auth.credentialsGroup")}</GroupLabel>
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label className="text-[11px] text-muted-foreground">{t("scan.auth.username")}</Label>
              <Input value={auth.username} onChange={(e) => setAuth({ username: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label className="text-[11px] text-muted-foreground">{t("scan.auth.password")}</Label>
              <Input type="password" value={auth.password} onChange={(e) => setAuth({ password: e.target.value })} />
            </div>
          </div>
          <div className="space-y-1">
            <Label className="text-[11px] text-muted-foreground">
              {t("scan.auth.totpSecret")} <span className="font-normal">({t("scan.auth.optional")})</span>
            </Label>
            <Input value={auth.totpSecret} onChange={(e) => setAuth({ totpSecret: e.target.value })} className="font-mono" />
          </div>
          <div className="space-y-1.5">
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <Checkbox checked={auth.emailLoginEnabled} onCheckedChange={(v) => setAuth({ emailLoginEnabled: v === true })} />
              <span className="text-xs">{t("scan.auth.emailLoginToggle")}</span>
            </label>
            {auth.emailLoginEnabled && (
              <div className="space-y-2 pl-6">
                <div className="grid grid-cols-2 gap-2">
                  <div className="space-y-1">
                    <Label className="text-[11px] text-muted-foreground">{t("scan.auth.emailAddress")}</Label>
                    <Input value={auth.emailAddress} onChange={(e) => setAuth({ emailAddress: e.target.value })} />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-[11px] text-muted-foreground">{t("scan.auth.emailPassword")}</Label>
                    <Input type="password" value={auth.emailPassword} onChange={(e) => setAuth({ emailPassword: e.target.value })} />
                  </div>
                </div>
                <div className="space-y-1">
                  <Label className="text-[11px] text-muted-foreground">
                    {t("scan.auth.emailTotp")} <span className="font-normal">({t("scan.auth.optional")})</span>
                  </Label>
                  <Input value={auth.emailTotp} onChange={(e) => setAuth({ emailTotp: e.target.value })} className="font-mono" />
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="space-y-1.5">
        <Label className="text-xs font-medium">
          {t("scan.auth.loginFlowLabel")} <span className="font-normal text-muted-foreground">({t("scan.auth.optional")})</span>
        </Label>
        <Textarea
          value={auth.loginFlow}
          onChange={(e) => setAuth({ loginFlow: e.target.value })}
          rows={3}
          placeholder={t("scan.auth.loginFlowHint")}
          className="font-mono text-xs"
        />
      </div>

      {authErr && <div className="text-destructive text-xs">{authErr}</div>}

      {/* 保存为档案：与临时填写同处，存成工作区档案供以后 profile 模式复用（ws 未选时禁用提示）。 */}
      <div className="border-t border-border pt-2.5">
        {ws ? (
          <SaveAsProfileInline auth={auth} ws={ws} onSaved={onProfileSaved} />
        ) : (
          <div className="text-[11px] text-muted-foreground">{t("scan.fields.selectWsFirst")}</div>
        )}
      </div>
    </div>
  );
}

/** profile 模式：档案卡列表 → 选中档案详情 + 角色单选（Task 14 + 2026-08-06 重排）。
 *  - 档案列表来自 listAuthProfiles(ws)（ws 隔离；ws 未选时不发请求，显示「先选工作区」提示）。
 *  - 左列档案卡：名字 + host + 角色数 + 整体验证徽章（可用/不可用/未验证）。点选 → setAuth({profileId})。
 *  - 右列详情：选中档案 login_url + 类型；角色行（role · username + 各自验证徽章），点选 → setAuth({credentialId})。
 *  - 切档案 → 清空 credentialId（防残留旧角色 id 指向新档案里不存在的角色）。 */
function ProfilePicker({ auth, setAuth, workspace, refreshSignal }: {
  auth: AuthFormState;
  setAuth: (patch: Partial<AuthFormState>) => void;
  workspace: string;
  refreshSignal: number;
}) {
  const { t } = useTranslation();
  const [profiles, setProfiles] = useState<AuthProfile[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    if (!workspace) {
      setProfiles([]);
      setLoading(false);
      setLoadFailed(false);
      return;
    }
    setLoading(true);
    setLoadFailed(false);
    listAuthProfiles(workspace)
      .then((list) => setProfiles(list))
      .catch(() => {
        setProfiles([]);
        setLoadFailed(true);
      })
      .finally(() => setLoading(false));
  }, [workspace, refreshSignal]);

  const selected = profiles.find((p) => p.id === auth.profileId);

  if (!workspace) {
    return <div className="text-xs text-muted-foreground">{t("scan.fields.selectWsFirst")}</div>;
  }
  if (loading) {
    return <div className="text-xs text-muted-foreground">{t("common.loading")}</div>;
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)] gap-4 items-start">
      {/* 左：档案卡列表 */}
      <div className="space-y-2">
        <GroupLabel>{t("scan.auth.selectProfileLabel")}</GroupLabel>
        {profiles.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border bg-card p-3 text-xs text-muted-foreground">
            {loadFailed ? t("common.loadFailed") : t("authProfiles.empty")}
          </div>
        ) : (
          profiles.map((p) => {
            const ov = overallState(p.credentials);
            const ob = verifyBadge(ov);
            const sel = p.id === auth.profileId;
            return (
              <button
                key={p.id}
                type="button"
                onClick={() => setAuth({ profileId: p.id, credentialId: "" })}
                aria-pressed={sel}
                className={`w-full text-left rounded-lg border p-3 transition-colors flex flex-col gap-1.5 ${
                  sel ? "border-primary bg-primary/5 shadow-sm" : "border-border bg-card hover:border-foreground/20"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className="text-[13px] font-semibold">{p.name}</span>
                  {p.scope === "system" && (
                    <span
                      className="inline-flex items-center rounded-full border border-border bg-muted/40 px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground"
                      title={t("authProfiles.systemHint")}
                    >
                      {t("authProfiles.systemBadge")}
                    </span>
                  )}
                  <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10.5px] font-semibold ${ob.cls}`}>
                    <span aria-hidden>{ob.icon}</span>
                    {t(`authProfiles.overall.${ov}`)}
                  </span>
                </div>
                <div className="flex items-center gap-2 text-[11px] font-mono text-muted-foreground">
                  <span>{hostOf(p.login_url)}</span>
                  <span>·</span>
                  <span>{p.credentials.length} {t("authProfiles.credentials")}</span>
                </div>
              </button>
            );
          })
        )}
      </div>

      {/* 右：选中档案详情 + 角色单选 */}
      {selected ? (
        <div className="space-y-3 rounded-lg border border-border bg-secondary p-3.5">
          <div>
            <div className="text-[14px] font-semibold">{selected.name}</div>
            <div className="flex items-center gap-2 text-xs mt-1.5">
              <span className="font-mono text-muted-foreground">{selected.login_url}</span>
              <span className="inline-flex items-center rounded-full bg-background px-2 py-0.5 text-[10.5px] font-semibold text-muted-foreground">
                {t(`scan.auth.loginType.${selected.login_type}`)}
              </span>
            </div>
          </div>
          <div className="border-t border-border" />
          <div>
            <div className="mb-2">
              <GroupLabel>{t("scan.auth.selectRole")}</GroupLabel>
            </div>
            <div className="space-y-2">
              {selected.credentials.map((c) => {
                const st = c.verify_status?.state ?? "unverified";
                const b = verifyBadge(st);
                const sel = c.id === auth.credentialId;
                return (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => setAuth({ credentialId: c.id })}
                    aria-pressed={sel}
                    className={`w-full flex items-center gap-2.5 rounded-lg border p-2.5 transition-colors text-left ${
                      sel ? "border-primary bg-primary/5 shadow-sm" : "border-border bg-card hover:border-foreground/20"
                    }`}
                  >
                    <span className={`w-4 h-4 rounded-full border-2 flex-none flex items-center justify-center ${sel ? "border-primary" : "border-input"}`}>
                      {sel && <span className="w-2 h-2 rounded-full bg-primary" />}
                    </span>
                    <div className="flex-1 min-w-0">
                      <span className="text-[13px] font-medium font-mono">
                        {c.role} <span className="font-normal text-muted-foreground">· {c.username}</span>
                      </span>
                      {st === "failed" && c.verify_status?.failure_detail && (
                        <div className="text-[11px] text-red/80 mt-0.5">{c.verify_status.failure_detail}</div>
                      )}
                    </div>
                    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10.5px] font-semibold flex-none ${b.cls}`}>
                      <span aria-hidden>{b.icon}</span>
                      {t(`authProfiles.verify.${st}`)}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
          <div className="border-t border-border" />
          <div className="flex items-start gap-1.5 text-[11px] text-muted-foreground leading-relaxed">
            <Info className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
            <span>{t("scan.auth.profileRoleHint")}</span>
          </div>
        </div>
      ) : (
        <div className="rounded-lg border border-dashed border-border bg-card p-4 text-xs text-muted-foreground">
          {t("scan.auth.selectProfileHint")}
        </div>
      )}
    </div>
  );
}

export function ScanFormFields({
  type,
  f,
  set,
  sourceErr,
  reuseErr,
  urlErr,
  authErr,
  workspace,
  wsList,
  onWorkspaceChange,
  wsLoading,
  presetReuseScanId,
}: Props) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [repos, setRepos] = useState<Repo[]>([]);
  const [addOpen, setAddOpen] = useState(false);
  // 黑盒「复用白盒结果」候选：当前 ws 的 whitebox scans（按 created_at 倒序，listScans 契约）。
  const [wbScans, setWbScans] = useState<ScanSummary[]>([]);
  // 标记「候选已为哪个 ws 加载完成」——smart-default 据此判断，避免依赖 wbLoading（effect 同帧
  // 读到旧值导致提前翻转：ws 刚选定时 listScans 的 setWbLoading(true) 尚未提交）。
  const [wbLoadedFor, setWbLoadedFor] = useState<string | null>(null);
  // ProfilePicker 刷新信号：inline 保存为新档案后递增 -> 触发重拉（须在所有 early return 之前，
  // 守 hooks 规则--白盒提前 return 不跳过此 useState）。onProfileSaved 在黑盒区定义（用 setAuth）。
  const [profileRefresh, setProfileRefresh] = useState(0);
  // 黑盒认证折叠态：默认跟随 auth.enabled（重跑预填 enabled=true → 自动展开，露出预填配置）。
  // 须在白盒 early return 之前（守 hooks 规则）。
  const [authExpanded, setAuthExpanded] = useState(() => f.auth.enabled);

  // P2: repo 列表按选定 ws 拉取——ws 未选时不发起（路径无意义）
  useEffect(() => {
    if (!workspace) {
      setRepos([]);
      return;
    }
    listRepos(workspace).then(setRepos).catch(() => {});
  }, [workspace, addOpen]);

  // 黑盒复用候选：按选定 ws 拉取其 whitebox scans。ws 切换 -> 旧 scan_id 失效，清空待重选。
  // 重跑预填（presetReuseScanId）：首帧保留预填值（已在 f.reuseScanId），仅拉候选验证，
  // 跳过清空与「默认选最新」；ws 切换后走原逻辑（预填仅对原 ws 一次性生效）。
  const presetDoneRef = useRef(false);
  useEffect(() => {
    if (type !== "blackbox" || !workspace) {
      setWbScans([]);
      setWbLoadedFor(null);
      return;
    }
    if (!presetDoneRef.current && presetReuseScanId) {
      presetDoneRef.current = true;
      listScans(workspace)
        .then((all) => {
          setWbScans(all.filter((s) => s.scan_type === "whitebox"));
          setWbLoadedFor(workspace);
        })
        .catch(() => {
          setWbScans([]);
          setWbLoadedFor(workspace);
        });
      return;
    }
    presetDoneRef.current = true;
    set({ reuseScanId: "" });
    listScans(workspace)
      .then((all) => {
        setWbScans(all.filter((s) => s.scan_type === "whitebox"));
        setWbLoadedFor(workspace);
      })
      .catch(() => {
        setWbScans([]);
        setWbLoadedFor(workspace);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type, workspace]);

  // 默认选最新一条白盒（listScans 倒序，[0] = 最新）——复用「最新白盒」直觉，但显式可选。
  // wbLoadedFor===workspace 守卫：等候选确为当前 ws 加载完才选，避免 ws 切换瞬间旧候选误选。
  useEffect(() => {
    if (type === "blackbox" && !f.reuseScanId && wbLoadedFor === workspace && wbScans.length > 0) {
      set({ reuseScanId: wbScans[0].scan_id });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type, f.reuseScanId, wbScans, wbLoadedFor, workspace]);

  const selectedRepoState = repos.find((r) => r.name === f.selectedRepo)?.state;

  // —— 共用：仓库选择器（入口已收窄——仅工作区已下载仓库，无本地路径分支） ——
  // ws 未选时不渲染仓库 picker / 添加按钮（listRepos 必须 ws）
  const repoPicker = workspace ? (
    <div className="space-y-2">
      <RepoCombobox
        repos={repos}
        value={f.selectedRepo || null}
        onChange={(v) => set({ selectedRepo: v })}
        placeholder={t("scan.repo.selectPlaceholder")}
        searchPlaceholder={t("scan.repo.searchPlaceholder")}
        emptyText={t("scan.repo.noMatch")}
        ungroupedLabel={t("scan.repo.ungrouped")}
        linkedLabel={t("repos.linkedBadge")}
      />
      <Button variant="outline" size="sm" onClick={() => setAddOpen(true)}>{t("scan.repo.addBtn")}</Button>
      {f.selectedRepo && selectedRepoState && selectedRepoState !== "ready" && (
        selectedRepoState === "cloning" || selectedRepoState === "pulling"
          ? <CloneProgress ws={workspace} name={f.selectedRepo} />
          : <div className="text-xs text-destructive">{t("scan.repo.notReady", { state: selectedRepoState })}</div>
      )}
      <AddRepoDialog ws={workspace} open={addOpen} onOpenChange={setAddOpen}
        onCreated={(name) => set({ selectedRepo: name })} />
    </div>
  ) : (
    <div className="text-xs text-muted-foreground">{t("scan.fields.selectWsFirst")}</div>
  );

  // —— 共用：workspace 选择器（P2: 替代原自由文本 wsName + 自动派生 + 冲突检测） ——
  const wsEmpty = !wsLoading && wsList.length === 0;
  const wsSelectInner = (
    <>
      <Select value={workspace} onValueChange={onWorkspaceChange}>
        <SelectTrigger className="w-full font-mono">
          <SelectValue placeholder={t("scan.fields.wsSelectPlaceholder")} />
        </SelectTrigger>
        <SelectContent>
          {wsEmpty ? (
            <SelectItem value="__empty__" disabled>{t("scan.fields.wsEmptyOption")}</SelectItem>
          ) : wsList.map((w) => (
            <SelectItem key={w.name} value={w.name}>{w.name}</SelectItem>
          ))}
        </SelectContent>
      </Select>
      {wsEmpty && (
        <div className="flex items-start gap-1.5 text-xs text-amber">
          <AlertCircle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
          <span>{t(isAdmin ? "scan.fields.wsEmptyHintAdmin" : "scan.fields.wsEmptyHintUser")}</span>
        </div>
      )}
    </>
  );
  const workspaceField = (
    <div className="space-y-1.5">
      <Label className="text-xs font-medium">{t("scan.fields.wsSelectLabel")}</Label>
      {wsSelectInner}
    </div>
  );

  // —— 白盒布局：Step 1 工作区（容器，解锁 repo）→ Step 2 代码源（仅仓库）——
  // 白盒已去动态（recon 固定静态，见 f2c64c8b）——纯离线源码审计，无目标 URL 输入；web_url 仅留作
  // 后端兼容签名（逻辑层不再使用），前端不再采集。
  // IA 不变量：repo 列表按 ws 隔离（listRepos(workspace)），故「选工作区」必须在「选仓库」之上。
  if (type === "whitebox") {
    return (
      <div className="flex flex-col gap-3.5">
        <StepGroup step={1} title={t("scan.steps.workspace")}>
          {workspaceField}
        </StepGroup>

        <StepGroup step={2} title={t("scan.steps.source")} tag={t("scan.tags.localAudit")} tagClass="bg-secondary text-muted-foreground">
          {repoPicker}
          {sourceErr && <div className="text-destructive text-xs">{sourceErr}</div>}
        </StepGroup>
      </div>
    );
  }

  // —— 黑盒布局（2026-08-06 重排）：目标服务 + 扫描上下文（工作区与复用白盒合并）+ 认证（默认折叠）。
  //   旧版 4 个等重 StepGroup（目标 / 工作区 / 代码上下文 / 认证）平铺显繁琐；新版按"必填核心 +
  //   可选增强"重排，去掉圆形步骤徽章与多余嵌套，认证默认折叠成一行（点「配置登录」展开）。
  // IA 不变量：repo 与白盒 scan 均按工作区隔离；URL 是黑盒主输入，保持在最上。
  const setAuth = (patch: Partial<AuthFormState>) => set({ auth: { ...f.auth, ...patch } });
  // inline 保存为新档案后：切 profile 模式 + 选中新建档案 + 递增 refreshSignal 触发 ProfilePicker 重拉。
  const onProfileSaved = (profile: AuthProfile) => {
    setAuth({ source: "profile", profileId: profile.id, credentialId: profile.credentials[0]?.id ?? "" });
    setProfileRefresh((n) => n + 1);
  };
  return (
    <div className="flex flex-col gap-5">
      {/* 目标服务 */}
      <section className="space-y-2">
        <div className="flex items-baseline gap-2">
          <h3 className="text-[13px] font-semibold">{t("scan.steps.targetService")}</h3>
          <span className="text-[10.5px] font-medium text-destructive">{t("scan.tags.required")}</span>
          <span className="ml-auto text-[11.5px] text-muted-foreground">{t("scan.fields.targetHint")}</span>
        </div>
        <Input
          id="url"
          value={f.url}
          onChange={(e) => set({ url: e.target.value })}
          placeholder={t("scan.fields.urlPlaceholder")}
          className="font-mono border-orange/30"
        />
        {urlErr && <div className="text-destructive text-xs">{urlErr}</div>}
        <div className="text-xs text-muted-foreground">{t("scan.fields.blackboxUrlHint")}</div>
      </section>

      <div className="border-t border-border" />

      {/* 扫描上下文：工作区 + 复用白盒（并排一条链） */}
      <section className="space-y-2.5">
        <div className="flex items-baseline gap-2">
          <h3 className="text-[13px] font-semibold">{t("scan.fields.contextLabel")}</h3>
          <span className="text-[10.5px] font-medium text-destructive">{t("scan.tags.required")}</span>
          <span className="ml-auto text-[11.5px] text-muted-foreground">{t("scan.fields.contextHint")}</span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,15rem)_minmax(0,1fr)] gap-3">
          <div className="space-y-1.5">
            <Label className="text-xs font-medium">{t("scan.fields.wsSelectLabel")}</Label>
            {wsSelectInner}
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs font-medium">{t("scan.fields.reuseSelectLabel")}</Label>
            {wbScans.length === 0 ? (
              <div className="rounded-lg border border-dashed border-border bg-card p-2.5 text-xs text-muted-foreground leading-relaxed">
                {workspace ? t("scan.fields.reuseEmpty") : t("scan.fields.selectWsFirst")}
              </div>
            ) : (
              <Select value={f.reuseScanId} onValueChange={(v) => set({ reuseScanId: v })}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder={t("scan.fields.reuseSelectPlaceholder")} />
                </SelectTrigger>
                <SelectContent>
                  {wbScans.map((s, i) => (
                    <SelectItem key={s.scan_id} value={s.scan_id}>
                      <span className="font-mono text-xs">{s.workflow_id ?? s.scan_id}</span>
                      {i === 0 && (
                        <span className="ml-1.5 inline-flex items-center rounded-full bg-primary/10 px-1.5 py-0.5 text-[9.5px] font-semibold text-primary">
                          {t("scan.fields.latestBadge")}
                        </span>
                      )}
                      <span className="ml-1.5 text-[11px] text-muted-foreground">
                        · {String(s.status)} · {s.vuln_count} {t("scan.fields.vulnsUnit")}
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            {/* 有候选却没选才提示；无候选时上方空态盒已说明，不再重复「请选择」红字。 */}
            {wbScans.length > 0 && reuseErr && <div className="text-destructive text-xs">{reuseErr}</div>}
          </div>
        </div>
      </section>

      <div className="border-t border-border" />

      {/* 认证登录（可选，默认折叠成一行） */}
      <section>
        <div className="flex items-center gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h3 className="text-[13px] font-semibold">{t("scan.steps.auth")}</h3>
              <span className="rounded-full bg-secondary px-2 py-0.5 text-[10.5px] font-semibold text-muted-foreground">
                {t("scan.tags.optional")}
              </span>
            </div>
            <div className="text-[11.5px] text-muted-foreground mt-0.5">
              {f.auth.enabled ? t("scan.auth.statusEnabled") : t("scan.auth.statusUnauth")}
            </div>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={() => setAuthExpanded((v) => !v)}>
            {authExpanded ? t("scan.auth.collapse") : t("scan.auth.configure")}
          </Button>
        </div>
        {authExpanded && (
          <div className="mt-4 pt-4 border-t border-border">
            <AuthControls
              auth={f.auth}
              setAuth={setAuth}
              authErr={authErr}
              workspace={workspace}
              refreshSignal={profileRefresh}
              onProfileSaved={onProfileSaved}
            />
          </div>
        )}
      </section>
    </div>
  );
}
