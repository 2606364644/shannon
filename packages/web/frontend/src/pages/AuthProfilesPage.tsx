// 认证档案库 页面(ws-child tab 内容)。范式镜像 UsersPage.tsx
// (refresh / loading / error / Card+Table / dialog 挂载) + ReposTab(useParams ws-scoped)。
// 结构: 档案列表 + 新建/编辑/删除 + 凭据行(占位, Task 12 扩展)。
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Trash2, Pencil } from "lucide-react";
import { listAuthProfiles, deleteAuthProfile } from "@/api/authProfiles";
import type { AuthProfile } from "@/api/types";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { AuthProfileDialog } from "@/components/AuthProfileDialog";
import { CredentialRow } from "./CredentialRow";

export function AuthProfilesPage() {
  const { t } = useTranslation();
  const { workspace } = useParams<{ workspace: string }>();
  const [profiles, setProfiles] = useState<AuthProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<AuthProfile | null>(null);
  const [delTarget, setDelTarget] = useState<AuthProfile | null>(null);

  async function refresh() {
    if (!workspace) return;
    setLoading(true); setError(null);
    try {
      setProfiles(await listAuthProfiles(workspace));
    } catch {
      setError(t("authProfiles.createFailed"));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { void refresh(); }, [workspace]); // eslint-disable-line react-hooks/exhaustive-deps

  async function onDelete() {
    if (!workspace || !delTarget) return;
    try {
      await deleteAuthProfile(workspace, delTarget.id);
      toast.success(t("authProfiles.deleted"));
      setDelTarget(null);
      void refresh();
    } catch {
      toast.error(t("authProfiles.createFailed"));
    }
  }

  if (!workspace) return null;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-medium">{t("authProfiles.title")}</h3>
        <Button variant="cta" onClick={() => setCreateOpen(true)}>
          {t("authProfiles.create")}
        </Button>
      </div>
      {loading ? <Skeleton className="h-20 w-full" />
       : error ? <div className="text-sm text-destructive">{error}</div>
       : profiles.length === 0 ? <Card className="p-6 text-sm text-muted-foreground">{t("authProfiles.empty")}</Card>
       : <Card><Table><TableHeader><TableRow>
            <TableHead>{t("authProfiles.name")}</TableHead>
            <TableHead>{t("authProfiles.loginUrl")}</TableHead>
            <TableHead>{t("authProfiles.credentials")}</TableHead>
            <TableHead></TableHead></TableRow></TableHeader>
            <TableBody>{profiles.map((p) => (
              <TableRow key={p.id}>
                <TableCell className="font-mono">
                  {p.name}
                  {p.scope === "system" && (
                    <span
                      className="ml-2 inline-flex items-center rounded-full border border-border bg-muted/40 px-2 py-0.5 text-[10.5px] font-semibold text-muted-foreground align-middle"
                      title={t("authProfiles.systemHint")}
                    >
                      {t("authProfiles.systemBadge")}
                    </span>
                  )}
                </TableCell>
                <TableCell className="font-mono text-xs">{p.login_url}</TableCell>
                <TableCell>
                  <div className="space-y-2">
                    {p.credentials.map((c) => (
                      <CredentialRow key={c.id} ws={workspace} profile={p} credential={c} onChanged={refresh} />
                    ))}
                  </div>
                </TableCell>
                <TableCell className="text-right">
                  {p.scope !== "system" && (
                    <>
                      <Button variant="ghost" size="icon" aria-label={t("authProfiles.edit")} onClick={() => setEditTarget(p)}>
                        <Pencil className="size-4" />
                      </Button>
                      <Button variant="ghost" size="icon" aria-label={t("authProfiles.delete")} onClick={() => setDelTarget(p)}>
                        <Trash2 className="size-4" />
                      </Button>
                    </>
                  )}
                </TableCell>
              </TableRow>))}</TableBody></Table></Card>}

      <AuthProfileDialog ws={workspace} open={createOpen} onOpenChange={setCreateOpen} onSaved={refresh} />
      {editTarget && (
        <AuthProfileDialog
          ws={workspace}
          open
          onOpenChange={(o) => !o && setEditTarget(null)}
          onSaved={() => { setEditTarget(null); void refresh(); }}
          editing={editTarget}
        />
      )}
      <Dialog open={!!delTarget} onOpenChange={(o) => !o && setDelTarget(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t("authProfiles.delete")}</DialogTitle></DialogHeader>
          <p className="text-sm text-destructive">{delTarget?.name}</p>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDelTarget(null)}>{t("common.cancel")}</Button>
            <Button variant="destructive" onClick={onDelete}>{t("authProfiles.delete")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
