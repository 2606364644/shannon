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
import type { FormState, AuthFormState, LoginType } from "../pages/ScanNewPage";
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

/** 步骤分组容器：圆角 + secondary 背景 + 边框 */
function StepGroup({ step, title, tag, tagClass, className, children }: {
  step: number;
  title: string;
  tag?: string;
  tagClass?: string;
  /** 根容器附加 class（如 max-w-2xl 限宽--黑盒 inline 模式 Step4 全宽时，Step1-3 仍限宽） */
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

/** 黑盒 Step4 登录配置区。auth-profile-vault（Task 14）后拆为两块，服从既有设计语言
 *  （StepGroup 容器 + 现有 Input/Label/Select/Switch/Checkbox，不引入新视觉）：
 *    - AuthControls：启用开关 + 登录来源 Select（inline 临时填写 / profile 使用档案）。
 *        profile 模式在此显 ProfilePicker；inline 模式此处仅作控制，字段交给 InlineAuthFields。
 *    - InlineAuthFields：inline 模式的完整 Authentication schema 字段（login_type / login_url /
 *        credentials[username/password/totp/email_login] / login_flow），对齐 core Authentication。
 *
 *  布局（2026-08-05）：inline 模式不再纵向堆叠撑高 Step4——Step4 突破 max-w-2xl 成全宽双列卡片，
 *    左列=AuthControls（开关/来源/提示），右列=InlineAuthFields（凭据/登录步骤），利用右侧空白。
 *    非 inline（关闭 / profile）保持 max-w-2xl 单栏：profile 仅两个 Select，无需展宽。 */
function AuthControls({ auth, setAuth, authErr, workspace, refreshSignal }: {
  auth: AuthFormState;
  setAuth: (patch: Partial<AuthFormState>) => void;
  authErr: string | null;
  /** ProfilePicker 拉档案的 workspace scope（auth-profiles 按 ws 隔离）。 */
  workspace: string;
  /** 透传 ProfilePicker：外部保存新档案后递增触发重拉。 */
  refreshSignal: number;
}) {
  const { t } = useTranslation();
  if (!auth.enabled) {
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <Label className="text-xs font-medium">{t("scan.auth.enableLabel")}</Label>
          <Switch checked={false} onCheckedChange={(v) => setAuth({ enabled: v })} />
        </div>
        <div className="text-xs text-muted-foreground">{t("scan.auth.enableHint")}</div>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <Label className="text-xs font-medium">{t("scan.auth.enableLabel")}</Label>
        <Switch checked onCheckedChange={(v) => setAuth({ enabled: v })} />
      </div>

      {/* 登录来源：inline（临时填写，旧行为）/ profile（使用档案，Task 14）。
          disabled 用 enabled=false 表达——折叠即关闭，故 Select 仅两态。 */}
      <div className="space-y-1.5">
        <Label className="text-xs font-medium">{t("scan.auth.sourceLabel")}</Label>
        <Select value={auth.source} onValueChange={(v) => setAuth({ source: v as "inline" | "profile" })}>
          <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="inline">{t("authProfiles.sourceInline")}</SelectItem>
            <SelectItem value="profile">{t("authProfiles.sourceProfile")}</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {auth.source === "profile" && <ProfilePicker auth={auth} setAuth={setAuth} workspace={workspace} refreshSignal={refreshSignal} />}

      {/* profile 模式校验错误贴 ProfilePicker 显；inline 模式错误交 InlineAuthFields 显，避免左右重复。 */}
      {auth.source === "profile" && authErr && <div className="text-destructive text-xs">{authErr}</div>}
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
    <form onSubmit={onSave} className="space-y-2 rounded-lg border border-dashed border-border bg-card p-2.5">
      <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {t("scan.auth.saveAsProfile")}
      </span>
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

/** inline 模式字段块（登录方式 / 登录地址 / 凭据 / 登录步骤）--渲染在 Step4 全宽双列的右列。
 *  字段经 setAuth 回写 FormState.auth；buildBody 转 ScanAuthentication 发后端。
 *  底部接 SaveAsProfileInline：把当前填的临时配置存成工作区档案（保存能力与填写区在一起）。 */
function InlineAuthFields({ auth, setAuth, authErr, ws, onProfileSaved }: {
  auth: AuthFormState;
  setAuth: (patch: Partial<AuthFormState>) => void;
  authErr: string | null;
  /** 保存为档案的 workspace scope（auth-profiles 按 ws 隔离）。 */
  ws: string;
  /** 保存成功回调：父级切 profile 模式 + 刷新 ProfilePicker + 选中新建档案。 */
  onProfileSaved: (profile: AuthProfile) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-3">
      <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {t("scan.auth.inlineFieldsTitle")}
      </span>
      <div className="space-y-1.5">
        <Label className="text-xs font-medium">{t("scan.auth.loginTypeLabel")}</Label>
        <Select value={auth.loginType} onValueChange={(v) => setAuth({ loginType: v as LoginType })}>
          <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
          <SelectContent>
            {(["form", "sso", "api", "basic"] as const).map((v) => (
              <SelectItem key={v} value={v}>{t(`scan.auth.loginType.${v}`)}</SelectItem>
            ))}
          </SelectContent>
        </Select>
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

      <div className="space-y-2 border-t border-border pt-2.5">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {t("scan.auth.credentialsGroup")}
        </span>
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
                <Label className="text-[11px] text-muted-foreground">{t("scan.auth.emailTotp")} <span className="font-normal">({t("scan.auth.optional")})</span></Label>
                <Input value={auth.emailTotp} onChange={(e) => setAuth({ emailTotp: e.target.value })} className="font-mono" />
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="space-y-1.5 border-t border-border pt-2.5">
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

      {/* 保存为档案：与临时填写同处右列，存成工作区档案供以后 profile 模式复用（ws 未选时禁用提示）。 */}
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
/** profile 模式：档案 Select -> 角色 Select（Task 14）。
 *  - 档案列表来自 listAuthProfiles(ws)（ws 隔离；ws 未选时不发请求，显示「先选工作区」提示）。
 *  - 选定档案后从 profile.credentials[] 渲染角色 Select（label: role · username，对齐 CredentialRow）。
 *  - 切档案 → 清空 credentialId（防残留旧角色 id 指向新档案里不存在的角色）。 */
function ProfilePicker({ auth, setAuth, workspace, refreshSignal }: {
  auth: AuthFormState;
  setAuth: (patch: Partial<AuthFormState>) => void;
  workspace: string;
  /** 外部保存新档案后递增 -> 触发重拉（否则新建档案不在选项里）。 */
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
  const creds = selected?.credentials ?? [];

  return (
    <div className="space-y-2">
      <div className="space-y-1.5">
        <Label className="text-xs font-medium">{t("authProfiles.name")}</Label>
        {!workspace ? (
          <div className="text-xs text-muted-foreground">{t("scan.fields.selectWsFirst")}</div>
        ) : loading ? (
          <div className="text-xs text-muted-foreground">{t("common.loading")}</div>
        ) : profiles.length === 0 ? (
          <div className="text-xs text-muted-foreground">
            {loadFailed ? t("common.loadFailed") : t("authProfiles.empty")}
          </div>
        ) : (
          <Select
            value={auth.profileId}
            onValueChange={(v) => setAuth({ profileId: v, credentialId: "" })}
          >
            <SelectTrigger className="w-full">
              <SelectValue placeholder={t("authProfiles.selectProfile")} />
            </SelectTrigger>
            <SelectContent>
              {profiles.map((p) => (
                <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>
      {auth.profileId && creds.length > 0 && (
        <div className="space-y-1.5">
          <Label className="text-xs font-medium">{t("authProfiles.role")}</Label>
          <Select value={auth.credentialId} onValueChange={(v) => setAuth({ credentialId: v })}>
            <SelectTrigger className="w-full">
              <SelectValue placeholder={t("authProfiles.selectCredential")} />
            </SelectTrigger>
            <SelectContent>
              {creds.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  <span className="font-mono">{c.role} · {c.username}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
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
  const workspaceField = (
    <div className="space-y-1.5">
      <Label className="text-xs font-medium">{t("scan.fields.wsSelectLabel")}</Label>
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

  // —— 黑盒布局：Step 1 目标服务 → Step 2 工作区 → Step 3 代码上下文（复用白盒结果 / 指定仓库 二选一）——
  // IA 不变量：repo 与白盒 scan 均按工作区隔离，「选工作区」必须在「选源」之上；URL 是黑盒主输入，保持 Step 1。
  // inline 模式（enabled + source=inline）时 Step4 突破 max-w-2xl 成全宽双列--Step1-3 仍 max-w-2xl 紧凑，
  // Step4 左列=AuthControls / 右列=InlineAuthFields，利用右侧空白避免纵向撑高（2026-08-05）。
  const isInlineAuth = f.auth.enabled && f.auth.source === "inline";
  const setAuth = (patch: Partial<AuthFormState>) => set({ auth: { ...f.auth, ...patch } });
  // inline 保存为新档案后：切 profile 模式 + 选中新建档案 + 递增 refreshSignal 触发 ProfilePicker 重拉。
  const onProfileSaved = (profile: AuthProfile) => {
    setAuth({ source: "profile", profileId: profile.id, credentialId: profile.credentials[0]?.id ?? "" });
    setProfileRefresh((n) => n + 1);
  };
  return (
    <div className="flex flex-col gap-3.5">
      <StepGroup
        step={1}
        title={t("scan.steps.targetService")}
        tag={t("scan.tags.required")}
        tagClass="text-[10px] text-destructive font-medium"
        className="max-w-2xl w-full"
      >
        <div className="space-y-1.5">
          <Label htmlFor="url" className="text-xs font-medium">{t("scan.fields.urlLabel")}</Label>
          <Input
            id="url"
            value={f.url}
            onChange={(e) => set({ url: e.target.value })}
            placeholder={t("scan.fields.urlPlaceholder")}
            className="font-mono border-orange/30"
          />
          {urlErr && <div className="text-destructive text-xs">{urlErr}</div>}
          <div className="text-xs text-muted-foreground">{t("scan.fields.blackboxUrlHint")}</div>
        </div>
      </StepGroup>

      <StepGroup step={2} title={t("scan.steps.workspace")} className="max-w-2xl w-full">
        {workspaceField}
      </StepGroup>

      <StepGroup step={3} title={t("scan.steps.codeContext")} tag={t("scan.tags.required")} tagClass="text-[10px] text-destructive font-medium" className="max-w-2xl w-full">
        {/* 黑盒恒复用白盒结果（exploitation-only）——无 repo/standalone 分支。 */}
        <div className="space-y-1.5">
          <Label className="text-xs font-medium">{t("scan.fields.reuseSelectLabel")}</Label>
          {wbScans.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border bg-card p-3 text-xs text-muted-foreground leading-relaxed">
              {workspace
                ? t("scan.fields.reuseEmpty")
                : t("scan.fields.selectWsFirst")}
            </div>
          ) : (
            <>
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
              <div className="text-[11px] text-muted-foreground">
                {t("scan.fields.reuseCount", { count: wbScans.length })}
              </div>
            </>
          )}
          {/* 有候选却没选才提示；无候选时上方空态盒已说明，不再重复「请选择」红字。 */}
          {wbScans.length > 0 && reuseErr && <div className="text-destructive text-xs">{reuseErr}</div>}
        </div>
      </StepGroup>

      {!isInlineAuth && (
        <StepGroup step={4} title={t("scan.steps.auth")} tag={t("scan.tags.optional")} tagClass="text-[10px] text-muted-foreground font-normal" className="max-w-2xl w-full">
          <AuthControls auth={f.auth} setAuth={setAuth} authErr={authErr} workspace={workspace} refreshSignal={profileRefresh} />
        </StepGroup>
      )}
      {isInlineAuth && (
        <StepGroup step={4} title={t("scan.steps.auth")} tag={t("scan.tags.optional")} tagClass="text-[10px] text-muted-foreground font-normal">
          <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,42rem)_minmax(0,1fr)] gap-5 items-start">
            <AuthControls auth={f.auth} setAuth={setAuth} authErr={authErr} workspace={workspace} refreshSignal={profileRefresh} />
            <InlineAuthFields auth={f.auth} setAuth={setAuth} authErr={authErr} ws={workspace} onProfileSaved={onProfileSaved} />
          </div>
        </StepGroup>
      )}
    </div>
  );
}
