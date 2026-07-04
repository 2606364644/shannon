import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import type { FormState } from "../pages/ScanNewPage";

interface ScanFormFieldsProps {
  type: "whitebox" | "blackbox";
  f: FormState;
  set: (patch: Partial<FormState>) => void;
  conflict: string | null;
  // 暂保留（Task 7 移 inline 横幅时清）；本 task 未消费，故不解构以避 noUnusedParameters。
  onConflictDismiss: () => void;
}

export function ScanFormFields({ type, f, set, conflict }: ScanFormFieldsProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{type === "blackbox" ? "黑盒扫描" : "白盒扫描"}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">代码来源</legend>
          <div className="space-y-2">
            <Label htmlFor="sourceKind">来源类型</Label>
            <Select value={f.sourceKind} onValueChange={(v) => set({ sourceKind: v as "path" | "git" })}>
              <SelectTrigger id="sourceKind"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="path">本地路径</SelectItem>
                <SelectItem value="git">git URL</SelectItem>
              </SelectContent>
            </Select>
            <Label htmlFor="sourceValue">路径 / URL</Label>
            <Input
              id="sourceValue"
              value={f.sourceValue}
              onChange={(e) => set({ sourceValue: e.target.value })}
              placeholder={f.sourceKind === "path" ? "/root/code/foo" : "https://gitlab.example/foo.git"}
            />
          </div>
          {f.sourceKind === "git" && (
            <div className="space-y-2 git-extra">
              <div className="flex gap-2">
                <Input value={f.branch} onChange={(e) => set({ branch: e.target.value })} placeholder="分支(可选)" />
                <Input value={f.commit} onChange={(e) => set({ commit: e.target.value })} placeholder="commit(可选,优先)" />
              </div>
              <div className="flex items-center gap-2">
                <Checkbox id="forceReclone" checked={f.forceReclone} onCheckedChange={(v) => set({ forceReclone: !!v })} />
                <Label htmlFor="forceReclone">强制重新 clone</Label>
              </div>
            </div>
          )}
        </fieldset>

        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">扫描目标 + 命名</legend>
          <div className="space-y-2">
            <Label htmlFor="url">目标 URL</Label>
            <Input id="url" value={f.url} onChange={(e) => set({ url: e.target.value })} placeholder="http://example.com" />
          </div>
          <div className="space-y-2">
            <Label htmlFor="wsName">workspace 名</Label>
            <Input
              id="wsName"
              value={f.wsName}
              onChange={(e) => set({ wsName: e.target.value })}
              placeholder="空=自动 {repo}_{timestamp}"
            />
          </div>
        </fieldset>

        {type === "blackbox" && (
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">复用</legend>
            <div className="flex items-center gap-2">
              <Checkbox id="reuseLatest" checked={f.reuseLatest} onCheckedChange={(v) => set({ reuseLatest: !!v })} />
              <Label htmlFor="reuseLatest">复用最新白盒结果</Label>
            </div>
            <div className="trace">
              --latest 按 url 匹配；不勾选时后端传 --repo 显式 standalone，规避 CLI 软默认复用
            </div>
          </fieldset>
        )}

        {conflict && (
          <div className="confirm-dialog ev-warn">
            ⚠ workspace「{conflict}」已存在，CLI -w 语义=存在则恢复，将
            <b>断点续扫</b>（恢复已有进度）。
          </div>
        )}
      </CardContent>
    </Card>
  );
}
