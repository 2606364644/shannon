import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { createRepo, linkReposInDir, ApiError } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { FileSystemPicker } from "@/components/FileSystemPicker";

interface Props {
  /** P2: 仓库落在 ws 内，调用 createRepo(ws, body) / linkReposInDir(ws, body) */
  ws: string;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onCreated: (name: string) => void;
}

type Mode = "clone" | "linkdir";

export function AddRepoDialog({ ws, open, onOpenChange, onCreated }: Props) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [mode, setMode] = useState<Mode>("clone");
  const [url, setUrl] = useState("");
  const [branch, setBranch] = useState("");
  const [commit, setCommit] = useState("");
  const [group, setGroup] = useState("");
  const [linkDirPath, setLinkDirPath] = useState("");
  const [busy, setBusy] = useState(false);

  const urlOk = /^(https?:|git@|ssh:)/.test(url.trim());
  const linkDirOk = linkDirPath.trim() !== "";
  const canSubmit = mode === "clone" ? urlOk : linkDirOk;

  function reset() {
    setUrl(""); setBranch(""); setCommit(""); setGroup(""); setLinkDirPath("");
  }

  async function submit() {
    try {
      setBusy(true);
      if (mode === "clone") {
        const r = await createRepo(ws, {
          git_url: url.trim(),
          branch: branch.trim() || undefined,
          commit: commit.trim() || undefined,
          group: group.trim() || undefined,
        });
        onCreated(r.name);
      } else {
        // 批量关联目录：扫描父目录下所有 git 仓库；toast 汇报 imported/skipped
        const res = await linkReposInDir(ws, { path: linkDirPath.trim() });
        toast.success(t("repos.addDialog.linkDirResult",
          { imported: res.imported.length, skipped: res.skipped.length }));
        onCreated(res.imported[0]?.name ?? "");
      }
      onOpenChange(false);
      reset();
    } catch (e) {
      if (e instanceof ApiError) {
        if (mode === "clone" && e.status === 503) toast.error(t("repos.addDialog.errors.noCreds"));
        else if (mode === "clone" && e.status === 409) toast.error(t("repos.addDialog.errors.exists"));
        else if (mode === "linkdir" && e.status === 422) toast.error(t("repos.addDialog.errors.badPath"));
        else toast.error(t("repos.addDialog.errors.failed", { status: e.status }));
      } else {
        toast.error(t("repos.addDialog.errors.network"));
        console.error(`${mode} failed:`, e);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent onInteractOutside={(e) => e.preventDefault()} onEscapeKeyDown={(e) => { if (busy) e.preventDefault(); }}>
        <DialogHeader>
          <DialogTitle>{t("repos.addDialog.title")}</DialogTitle>
          <DialogDescription>
            {mode === "clone" ? t("repos.addDialog.desc") : t("repos.addDialog.linkDirDesc")}
          </DialogDescription>
        </DialogHeader>

        {/* 模式切换：克隆 git 仓库 / 批量关联目录（扫父目录下所有 git 仓库）。
            关联为 admin-only（任意磁盘路径较敏感），非 admin 仅可见克隆模式。 */}
        {isAdmin && (
          <div className="flex flex-wrap gap-2">
            <Button
              data-testid="mode-clone" size="sm"
              variant={mode === "clone" ? "default" : "outline"}
              onClick={() => setMode("clone")}
            >
              {t("repos.addDialog.modeClone")}
            </Button>
            <Button
              data-testid="mode-linkdir" size="sm"
              variant={mode === "linkdir" ? "default" : "outline"}
              onClick={() => setMode("linkdir")}
            >
              {t("repos.addDialog.modeLinkDir")}
            </Button>
          </div>
        )}

        {mode === "clone" ? (
          <div className="space-y-3">
            <div className="space-y-1">
              <Label htmlFor="repo-url">{t("repos.addDialog.urlLabel")}</Label>
              <Input id="repo-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder={t("repos.addDialog.urlPlaceholder")} />
              {!urlOk && url && <div className="text-xs text-destructive">{t("repos.addDialog.urlError")}</div>}
            </div>
            <div className="space-y-1">
              <Label htmlFor="repo-group">{t("repos.addDialog.groupLabel")}</Label>
              <Input id="repo-group" value={group} onChange={(e) => setGroup(e.target.value)} placeholder={t("repos.addDialog.groupPlaceholder")} />
            </div>
            <div className="flex gap-2">
              <Input value={branch} onChange={(e) => setBranch(e.target.value)} placeholder={t("repos.addDialog.branchPlaceholder")} />
              <Input value={commit} onChange={(e) => setCommit(e.target.value)} placeholder={t("repos.addDialog.commitPlaceholder")} />
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            <Label htmlFor="linkdir-path">{t("repos.addDialog.linkDirPathLabel")}</Label>
            <div className="flex gap-2">
              <Input data-testid="linkdir-path" id="linkdir-path" value={linkDirPath}
                     onChange={(e) => setLinkDirPath(e.target.value)}
                     placeholder={t("repos.addDialog.linkDirPathPlaceholder")} className="font-mono" />
              <FileSystemPicker value={linkDirPath} onChange={(v) => setLinkDirPath(v)}
                                triggerLabel={t("scan.fields.browse")}
                                title={t("repos.addDialog.linkDirPathLabel")} />
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
          <Button data-testid="submit" disabled={!canSubmit || busy} onClick={submit}>
            {mode === "clone" ? t("repos.addDialog.cloneBtn") : t("repos.addDialog.linkDirBtn")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
