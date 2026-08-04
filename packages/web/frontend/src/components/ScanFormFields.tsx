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
import type { Repo, ScanSummary, Workspace } from "@/api/types";
import type { FormState, AuthFormState, LoginType, SuccessConditionType } from "../pages/ScanNewPage";
import { useAuth } from "@/auth/AuthContext";
import { AlertCircle, Info } from "lucide-react";

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
function StepGroup({ step, title, tag, tagClass, children }: {
  step: number;
  title: string;
  tag?: string;
  tagClass?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border bg-secondary p-3.5 space-y-2.5">
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

/** 黑盒 Step4 登录配置区：Switch 启用 → 展开完整 Authentication schema 字段（对齐 core
 *  Authentication：login_type/login_url/credentials[username/password/totp/email_login]/login_flow
 *  /success_condition）。字段经 setAuth 回写 FormState.auth；buildBody 转 ScanAuthentication 发后端。
 *  服从既有设计语言：StepGroup 容器 + 现有 Input/Label/Select/Switch/Checkbox，不引入新视觉。 */
function AuthFields({ auth, setAuth, authErr }: {
  auth: AuthFormState;
  setAuth: (patch: Partial<AuthFormState>) => void;
  authErr: string | null;
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

      <div className="space-y-2 border-t border-border pt-2.5">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {t("scan.auth.successGroup")}
        </span>
        <div className="grid grid-cols-2 gap-2">
          <div className="space-y-1">
            <Label className="text-[11px] text-muted-foreground">{t("scan.auth.scTypeLabel")}</Label>
            <Select value={auth.scType} onValueChange={(v) => setAuth({ scType: v as SuccessConditionType })}>
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                {(["url_contains", "element_present", "url_equals_exactly", "text_contains"] as const).map((v) => (
                  <SelectItem key={v} value={v}>{t(`scan.auth.scType.${v}`)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label className="text-[11px] text-muted-foreground">{t("scan.auth.scValueLabel")}</Label>
            <Input value={auth.scValue} onChange={(e) => setAuth({ scValue: e.target.value })} className="font-mono" />
          </div>
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
      <div className="flex items-start gap-1.5 text-[11px] text-muted-foreground leading-relaxed">
        <Info className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
        <span>{t("scan.auth.infoNote")}</span>
      </div>
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
  return (
    <div className="flex flex-col gap-3.5">
      <StepGroup
        step={1}
        title={t("scan.steps.targetService")}
        tag={t("scan.tags.required")}
        tagClass="text-[10px] text-destructive font-medium"
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

      <StepGroup step={2} title={t("scan.steps.workspace")}>
        {workspaceField}
      </StepGroup>

      <StepGroup step={3} title={t("scan.steps.codeContext")} tag={t("scan.tags.required")} tagClass="text-[10px] text-destructive font-medium">
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

      <StepGroup step={4} title={t("scan.steps.auth")} tag={t("scan.tags.optional")} tagClass="text-[10px] text-muted-foreground font-normal">
        <AuthFields
          auth={f.auth}
          setAuth={(patch) => set({ auth: { ...f.auth, ...patch } })}
          authErr={authErr}
        />
      </StepGroup>
    </div>
  );
}
