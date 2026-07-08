import { useEffect, useState } from "react";
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

const UNGROUPED_LABEL = "未分组";
function groupRepos(repos: Repo[]): { name: string; repos: Repo[] }[] {
  const map = new Map<string, Repo[]>();
  for (const r of repos) {
    const g = r.group ?? UNGROUPED_LABEL;
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
  const [repos, setRepos] = useState<Repo[]>([]);
  const [addOpen, setAddOpen] = useState(false);

  useEffect(() => { listRepos().then(setRepos).catch(() => {}); }, [addOpen]);

  const selectedRepoState = repos.find((r) => r.name === f.selectedRepo)?.state;

  return (
    <Card>
      <CardHeader><CardTitle>{type === "blackbox" ? "黑盒扫描" : "白盒扫描"}</CardTitle></CardHeader>
      <CardContent className="space-y-6">
        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">选择仓库</legend>
          <Select value={f.sourceKind} onValueChange={(v) => set({ sourceKind: v as "repo" | "path" })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="repo">已下载仓库</SelectItem>
              <SelectItem value="path">本地路径</SelectItem>
            </SelectContent>
          </Select>

          {f.sourceKind === "repo" ? (
            <div className="space-y-2">
              <Select value={f.selectedRepo} onValueChange={(v) => set({ selectedRepo: v })}>
                <SelectTrigger><SelectValue placeholder="选择仓库" /></SelectTrigger>
                <SelectContent>
                  {groupRepos(repos).map((g) => (
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
              <Button variant="outline" size="sm" onClick={() => setAddOpen(true)}>+ 添加新仓库</Button>
              {f.selectedRepo && selectedRepoState && selectedRepoState !== "ready" && (
                selectedRepoState === "cloning" || selectedRepoState === "pulling"
                  ? <CloneProgress name={f.selectedRepo} />
                  : <div className="text-xs text-destructive">仓库未就绪（{selectedRepoState}）</div>
              )}
              <AddRepoDialog open={addOpen} onOpenChange={setAddOpen}
                onCreated={(name) => set({ selectedRepo: name })} />
            </div>
          ) : (
            <div className="space-y-2">
              <div className="flex gap-2">
                <Input value={f.sourceValue} onChange={(e) => set({ sourceValue: e.target.value })}
                  placeholder="/root/code/foo" />
                <FileSystemPicker value={f.sourceValue} onChange={(v) => set({ sourceValue: v })} triggerLabel="📁 浏览" />
              </div>
            </div>
          )}
          {sourceErr && <div className="text-destructive text-xs">{sourceErr}</div>}
        </fieldset>

        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">扫描目标 + 命名</legend>
          <div className="space-y-2">
            <Label htmlFor="url">
              目标 URL{type === "whitebox" && <span className="text-muted-foreground font-normal">（可选）</span>}
            </Label>
            <Input id="url" value={f.url} onChange={(e) => set({ url: e.target.value })} placeholder="http://example.com" />
            {urlErr && <div className="text-destructive text-xs">{urlErr}</div>}
            {type === "whitebox" && !f.url && (
              <div className="text-xs text-muted-foreground">可选；填了便于黑盒 --latest 按 URL 匹配本次白盒</div>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="wsName">workspace 名</Label>
            <Input id="wsName" value={f.wsName} onChange={(e) => set({ wsName: e.target.value })} placeholder="空=自动 {repo}_{timestamp}" />
            {loadingConflict && <div className="text-xs text-yellow">检测重名中…</div>}
            {!f.wsName && derivedName && <div className="text-xs text-muted-foreground">预览名：{derivedName}（预览，实际由后端生成）</div>}
          </div>
        </fieldset>

        {type === "blackbox" && (
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">复用</legend>
            <div className="flex items-center gap-2">
              <Checkbox id="reuseLatest" checked={f.reuseLatest} onCheckedChange={(v) => set({ reuseLatest: !!v })} />
              <Label htmlFor="reuseLatest">复用最新白盒结果</Label>
            </div>
            <div className="text-xs text-muted-foreground">--latest 按 url 匹配；不勾选时后端传 --repo 显式 standalone</div>
          </fieldset>
        )}
      </CardContent>
    </Card>
  );
}
