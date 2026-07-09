import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem, SelectGroup, SelectLabel } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { FileSystemPicker } from "./FileSystemPicker";
import { AddRepoDialog } from "./AddRepoDialog";
import { CloneProgress } from "./CloneProgress";
import { listRepos } from "@/api/client";
import type { Repo } from "@/api/types";
import type { FormState } from "../pages/ScanNewPage";

function groupRepos(repos: Repo[], t: TFunction): { name: string; repos: Repo[] }[] {
  const map = new Map<string, Repo[]>();
  for (const r of repos) {
    const g = r.group ?? t("scan.repo.ungrouped");
    let arr = map.get(g);
    if (!arr) { arr = []; map.set(g, arr); }
    arr.push(r);
  }
  return Array.from(map, ([name, rs]) => ({ name, repos: rs }));
}

interface Props {
  type: "whitebox" | "blackbox";
  f: FormState;
  set: (patch: Partial<FormState>) => void;
  sourceErr: string | null;
  urlErr: string | null;
  loadingConflict: boolean;
  derivedName: string;
}

export function ScanFormFields({ type, f, set, sourceErr, urlErr, loadingConflict, derivedName }: Props) {
  const { t } = useTranslation();
  const [repos, setRepos] = useState<Repo[]>([]);
  const [addOpen, setAddOpen] = useState(false);

  useEffect(() => { listRepos().then(setRepos).catch(() => {}); }, [addOpen]);

  const selectedRepoState = repos.find((r) => r.name === f.selectedRepo)?.state;

  return (
    <Card>
      <CardHeader><CardTitle>{t(`scan.cardTitle.${type}`)}</CardTitle></CardHeader>
      <CardContent className="space-y-6">
        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">{t("scan.fields.selectRepo")}</legend>
          <Select value={f.sourceKind} onValueChange={(v) => set({ sourceKind: v as "repo" | "path" })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="repo">{t("scan.options.downloadedRepo")}</SelectItem>
              <SelectItem value="path">{t("scan.options.localPath")}</SelectItem>
            </SelectContent>
          </Select>

          {f.sourceKind === "repo" ? (
            <div className="space-y-2">
              <Select value={f.selectedRepo} onValueChange={(v) => set({ selectedRepo: v })}>
                <SelectTrigger><SelectValue placeholder={t("scan.repo.selectPlaceholder")} /></SelectTrigger>
                <SelectContent>
                  {groupRepos(repos, t).map((g) => (
                    <SelectGroup key={g.name}>
                      <SelectLabel>{g.name}</SelectLabel>
                      {g.repos.map((r) => (
                        <SelectItem key={r.name} value={r.name}>
                          {r.name.split("/").pop() ?? r.name} — {r.source?.url ?? r.state}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  ))}
                </SelectContent>
              </Select>
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
            <div className="space-y-2">
              <div className="flex gap-2">
                <Input value={f.sourceValue} onChange={(e) => set({ sourceValue: e.target.value })}
                  placeholder={t("scan.fields.pathPlaceholder")} />
                <FileSystemPicker value={f.sourceValue} onChange={(v) => set({ sourceValue: v })} triggerLabel={t("scan.fields.browse")} />
              </div>
            </div>
          )}
          {sourceErr && <div className="text-destructive text-xs">{sourceErr}</div>}
        </fieldset>

        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">{t("scan.fields.targetName")}</legend>
          <div className="space-y-2">
            <Label htmlFor="url">
              {t("scan.fields.urlLabel")}{type === "whitebox" && <span className="text-muted-foreground font-normal">{t("scan.fields.urlOptionalSuffix")}</span>}
            </Label>
            <Input id="url" value={f.url} onChange={(e) => set({ url: e.target.value })} placeholder={t("scan.fields.urlPlaceholder")} />
            {urlErr && <div className="text-destructive text-xs">{urlErr}</div>}
            {type === "whitebox" && !f.url && (
              <div className="text-xs text-muted-foreground">{t("scan.fields.urlHint")}</div>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="wsName">{t("scan.fields.wsNameLabel")}</Label>
            <Input id="wsName" value={f.wsName} onChange={(e) => set({ wsName: e.target.value })} placeholder={t("scan.fields.wsNamePlaceholder")} />
            {loadingConflict && <div className="text-xs text-yellow">{t("scan.fields.checkingConflict")}</div>}
            {!f.wsName && derivedName && <div className="text-xs text-muted-foreground">{t("scan.fields.previewName", { name: derivedName })}</div>}
          </div>
        </fieldset>

        {type === "blackbox" && (
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">{t("scan.fields.reuse")}</legend>
            <div className="flex items-center gap-2">
              <Checkbox id="reuseLatest" checked={f.reuseLatest} onCheckedChange={(v) => set({ reuseLatest: !!v })} />
              <Label htmlFor="reuseLatest">{t("scan.fields.reuseLatest")}</Label>
            </div>
            <div className="text-xs text-muted-foreground">{t("scan.fields.reuseHint")}</div>
          </fieldset>
        )}
      </CardContent>
    </Card>
  );
}
