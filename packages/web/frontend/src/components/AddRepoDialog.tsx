import { useState } from "react";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { createRepo, ApiError } from "@/api/client";

interface Props {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onCreated: (name: string) => void;
}

export function AddRepoDialog({ open, onOpenChange, onCreated }: Props) {
  const [url, setUrl] = useState("");
  const [branch, setBranch] = useState("");
  const [commit, setCommit] = useState("");
  const [busy, setBusy] = useState(false);

  const urlOk = /^(https?:|git@|ssh:)/.test(url.trim());

  async function submit() {
    try {
      setBusy(true);
      const r = await createRepo({
        git_url: url.trim(),
        branch: branch.trim() || undefined,
        commit: commit.trim() || undefined,
      });
      onCreated(r.name);
      onOpenChange(false);
      setUrl(""); setBranch(""); setCommit("");
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 503) toast.error("未配置 git 凭证（GITLAB_USER/TOKEN）");
        else if (e.status === 409) toast.error("仓库已存在，可改用更新");
        else toast.error(`添加失败（${e.status}）`);
      } else {
        toast.error("添加失败（网络错误）");
        console.error("createRepo failed:", e);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent onInteractOutside={(e) => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle>添加仓库</DialogTitle>
          <DialogDescription>clone git 仓库到本地，之后可反复扫描。</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="repo-url">git URL</Label>
            <Input id="repo-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://gitlab.example/foo.git" />
            {!urlOk && url && <div className="text-xs text-destructive">需为 git URL（https: / git@ / ssh:）</div>}
          </div>
          <div className="flex gap-2">
            <Input value={branch} onChange={(e) => setBranch(e.target.value)} placeholder="分支(可选)" />
            <Input value={commit} onChange={(e) => setCommit(e.target.value)} placeholder="commit(可选)" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>取消</Button>
          <Button disabled={!urlOk || busy} onClick={submit}>clone</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
