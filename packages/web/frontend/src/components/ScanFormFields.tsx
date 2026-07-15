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
import type { Repo } from "@/api/types";
import type { FormState } from "../pages/ScanNewPage";

interface Props {
  type: "whitebox" | "blackbox";
  f: FormState;
  set: (patch: Partial<FormState>) => void;
  sourceErr: string | null;
  urlErr: string | null;
  loadingConflict: boolean;
  derivedName: string;
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

export function ScanFormFields({ type, f, set, sourceErr, urlErr, loadingConflict, derivedName }: Props) {
  const { t } = useTranslation();
  const [repos, setRepos] = useState<Repo[]>([]);
  const [addOpen, setAddOpen] = useState(false);

  useEffect(() => { listRepos().then(setRepos).catch(() => {}); }, [addOpen]);

  const selectedRepoState = repos.find((r) => r.name === f.selectedRepo)?.state;

  // —— 共用：代码源选择器 ——
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
              ? <CloneProgress name={f.selectedRepo} />
              : <div className="text-xs text-destructive">{t("scan.repo.notReady", { state: selectedRepoState })}</div>
          )}
          <AddRepoDialog open={addOpen} onOpenChange={setAddOpen}
            onCreated={(name) => set({ selectedRepo: name })} />
        </div>
      ) : (
        <div className="flex gap-2">
          <Input value={f.sourceValue} onChange={(e) => set({ sourceValue: e.target.value })}
            placeholder={t("scan.fields.pathPlaceholder")} />
          <FileSystemPicker value={f.sourceValue} onChange={(v) => set({ sourceValue: v })} triggerLabel={t("scan.fields.browse")} />
        </div>
      )}
      {sourceErr && <div className="text-destructive text-xs">{sourceErr}</div>}
    </>
  );

  // —— 共用：工作区名称 ——
  const workspaceField = (
    <div className="space-y-1.5">
      <Label htmlFor="wsName" className="text-xs font-medium">
        {t("scan.fields.wsNameLabel")}
        <span className="font-normal text-muted-foreground"> — {t("scan.fields.optional")}</span>
      </Label>
      <Input id="wsName" value={f.wsName} onChange={(e) => set({ wsName: e.target.value })} placeholder={t("scan.fields.wsNamePlaceholder")} />
      {loadingConflict && <div className="text-xs text-yellow">{t("scan.fields.checkingConflict")}</div>}
      {!f.wsName && derivedName && <div className="text-xs text-muted-foreground">{t("scan.fields.previewName", { name: derivedName })}</div>}
    </div>
  );

  // —— 白盒布局：Step 1 代码源 → Step 2 目标信息 ——
  if (type === "whitebox") {
    return (
      <div className="flex flex-col gap-3.5">
        <StepGroup step={1} title={t("scan.steps.source")} tag={t("scan.tags.localAudit")} tagClass="bg-primary/10 text-primary">
          {sourceSelector}
        </StepGroup>

        <StepGroup step={2} title={t("scan.steps.target")}>
          <div className="space-y-1.5">
            <Label htmlFor="url" className="text-xs font-medium">
              {t("scan.fields.urlLabel")}
              <span className="font-normal text-muted-foreground"> — {t("scan.fields.optional")}</span>
            </Label>
            <Input id="url" value={f.url} onChange={(e) => set({ url: e.target.value })} placeholder={t("scan.fields.urlPlaceholder")} />
            {urlErr && <div className="text-destructive text-xs">{urlErr}</div>}
            {!f.url && <div className="text-xs text-muted-foreground">{t("scan.fields.urlHint")}</div>}
          </div>
          {workspaceField}
        </StepGroup>
      </div>
    );
  }

  // —— 黑盒布局：Step 1 目标 URL → Step 2 代码上下文 → Step 3 工作区 ——
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
            className="border-orange/30"
          />
          {urlErr && <div className="text-destructive text-xs">{urlErr}</div>}
          <div className="text-xs text-muted-foreground">{t("scan.fields.blackboxUrlHint")}</div>
        </div>
      </StepGroup>

      <StepGroup step={2} title={t("scan.steps.codeContext")} tag={t("scan.tags.auxiliary")} tagClass="text-[10px] text-muted-foreground font-normal">
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

      <StepGroup step={3} title={t("scan.steps.workspace")}>
        {workspaceField}
      </StepGroup>
    </div>
  );
}
