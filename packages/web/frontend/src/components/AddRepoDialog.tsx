import { useState } from "react";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation();
  const [url, setUrl] = useState("");
  const [branch, setBranch] = useState("");
  const [commit, setCommit] = useState("");
  const [group, setGroup] = useState("");
  const [busy, setBusy] = useState(false);

  const urlOk = /^(https?:|git@|ssh:)/.test(url.trim());

  async function submit() {
    try {
      setBusy(true);
      const r = await createRepo({
        git_url: url.trim(),
        branch: branch.trim() || undefined,
        commit: commit.trim() || undefined,
        group: group.trim() || undefined,
      });
      onCreated(r.name);
      onOpenChange(false);
      setUrl(""); setBranch(""); setCommit(""); setGroup("");
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 503) toast.error(t("repos.addDialog.errors.noCreds"));
        else if (e.status === 409) toast.error(t("repos.addDialog.errors.exists"));
        else toast.error(t("repos.addDialog.errors.failed", { status: e.status }));
      } else {
        toast.error(t("repos.addDialog.errors.network"));
        console.error("createRepo failed:", e);
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
          <DialogDescription>{t("repos.addDialog.desc")}</DialogDescription>
        </DialogHeader>
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
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
          <Button disabled={!urlOk || busy} onClick={submit}>{t("repos.addDialog.cloneBtn")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
