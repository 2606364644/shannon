import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { FileSystemPicker } from "./FileSystemPicker";
import { RepoCombobox } from "./RepoCombobox";
import { AddRepoDialog } from "./AddRepoDialog";
import { CloneProgress } from "./CloneProgress";
import { listRepos } from "@/api/client";
import type { Repo, Workspace } from "@/api/types";
import type { FormState } from "../pages/ScanNewPage";
import { useAuth } from "@/auth/AuthContext";
import { AlertCircle } from "lucide-react";

interface Props {
  type: "whitebox" | "blackbox";
  f: FormState;
  set: (patch: Partial<FormState>) => void;
  sourceErr: string | null;
  urlErr: string | null;
  /** P2: 选定的目标 workspace——驱动 listRepos(ws) 与子组件 ws 参数 */
  workspace: string;
  /** P2: 用户可见的 ws 列表（P1 后端已过滤）——供下拉选项 */
  wsList: Workspace[];
  /** P2: ws 下拉变更回调 */
  onWorkspaceChange: (ws: string) => void;
  /** ws 列表加载中（防首帧 [] 误判为空态闪现提示） */
  wsLoading: boolean;
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

export function ScanFormFields({
  type,
  f,
  set,
  sourceErr,
  urlErr,
  workspace,
  wsList,
  onWorkspaceChange,
  wsLoading,
}: Props) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [repos, setRepos] = useState<Repo[]>([]);
  const [addOpen, setAddOpen] = useState(false);

  // P2: repo 列表按选定 ws 拉取——ws 未选时不发起（路径无意义）
  useEffect(() => {
    if (!workspace) {
      setRepos([]);
      return;
    }
    listRepos(workspace).then(setRepos).catch(() => {});
  }, [workspace, addOpen]);

  const selectedRepoState = repos.find((r) => r.name === f.selectedRepo)?.state;

  // —— 共用：代码源选择器 ——
  // P2: repo 模式下未选 ws 时显提示，不渲染仓库 picker / 添加按钮（listRepos 必须 ws）
  const sourceSelector = (
    <>
      <Select value={f.sourceKind} onValueChange={(v) => set({ sourceKind: v as "repo" | "path" })}>
        <SelectTrigger className="w-[180px]"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="repo">{t("scan.options.downloadedRepo")}</SelectItem>
          <SelectItem value="path">{t("scan.options.localPath")}</SelectItem>
        </SelectContent>
      </Select>

      {f.sourceKind === "repo" ? (
        workspace ? (
          <div className="space-y-2">
            <RepoCombobox
              repos={repos}
              value={f.selectedRepo || null}
              onChange={(v) => set({ selectedRepo: v })}
              placeholder={t("scan.repo.selectPlaceholder")}
              searchPlaceholder={t("scan.repo.searchPlaceholder")}
              emptyText={t("scan.repo.noMatch")}
              ungroupedLabel={t("scan.repo.ungrouped")}
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
        )
      ) : (
        <div className="flex gap-2">
          <Input value={f.sourceValue} onChange={(e) => set({ sourceValue: e.target.value })}
            placeholder={t("scan.fields.pathPlaceholder")} className="font-mono" />
          <FileSystemPicker value={f.sourceValue} onChange={(v) => set({ sourceValue: v })} triggerLabel={t("scan.fields.browse")} />
        </div>
      )}
      {sourceErr && <div className="text-destructive text-xs">{sourceErr}</div>}
    </>
  );

  // —— 共用：workspace 选择器（P2: 替代原自由文本 wsName + 自动派生 + 冲突检测） ——
  // 后端 resume 语义不变：扫到已有 ws 即追加 resumeAttempts；用户已显式选定，无需确认弹窗
  // 无可用工作区空态（排除加载中）：下拉显 disabled 占位项 + 下方按角色显引导。
  // 普通用户无 ws → 提示联系管理员；admin 无 ws（新部署）→ 提示去新建。
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

  // —— 白盒布局：Step 1 工作区（容器，解锁 repo）→ Step 2 代码源 → Step 3 目标地址（可选）——
  // IA 不变量：repo 列表按 ws 隔离（listRepos(workspace)），故「选工作区」必须在「选仓库」之上——
  // 表单顺序对齐依赖方向，避免用户先撞 Step1 仓库、发现选不了、再下滑选 ws、又上滑回选仓库。
  if (type === "whitebox") {
    return (
      <div className="flex flex-col gap-3.5">
        <StepGroup step={1} title={t("scan.steps.workspace")}>
          {workspaceField}
        </StepGroup>

        <StepGroup step={2} title={t("scan.steps.source")} tag={t("scan.tags.localAudit")} tagClass="bg-secondary text-muted-foreground">
          {sourceSelector}
        </StepGroup>

        <StepGroup step={3} title={t("scan.steps.target")}>
          <div className="space-y-1.5">
            <Label htmlFor="url" className="text-xs font-medium">
              {t("scan.fields.urlLabel")}
              <span className="font-normal text-muted-foreground"> — {t("scan.fields.optional")}</span>
            </Label>
            <Input id="url" value={f.url} onChange={(e) => set({ url: e.target.value })} placeholder={t("scan.fields.urlPlaceholder")} className="font-mono" />
            {urlErr && <div className="text-destructive text-xs">{urlErr}</div>}
            {!f.url && <div className="text-xs text-muted-foreground">{t("scan.fields.urlHint")}</div>}
          </div>
        </StepGroup>
      </div>
    );
  }

  // —— 黑盒布局：Step 1 目标服务 → Step 2 工作区（容器，解锁 repo）→ Step 3 代码上下文 ——
  // IA 不变量：repo 按工作区隔离，「选工作区」必须在「选仓库」之上；URL 是黑盒主输入，故保持 Step 1。
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

      <StepGroup step={3} title={t("scan.steps.codeContext")} tag={t("scan.tags.auxiliary")} tagClass="text-[10px] text-muted-foreground font-normal">
        {/* 复用开关 — 卡片样式 */}
        <div className="flex items-start gap-2 rounded-lg border border-border bg-card p-3">
          <Checkbox
            id="reuseLatest"
            checked={f.reuseLatest}
            onCheckedChange={(v) => set({ reuseLatest: !!v })}
            className="mt-0.5"
          />
          <div>
            <Label htmlFor="reuseLatest" className="text-xs font-medium cursor-pointer">{t("scan.fields.reuseLatest")}</Label>
            <div className="text-[11px] text-muted-foreground mt-0.5">{t("scan.fields.reuseHint")}</div>
          </div>
        </div>
        {sourceSelector}
      </StepGroup>
    </div>
  );
}
