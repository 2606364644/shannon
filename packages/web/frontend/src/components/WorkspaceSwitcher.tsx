import { useState, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { ArrowLeftRight, X, Pin, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useWorkspaces } from "@/api/useWorkspaces";
import { useAuth } from "@/auth/AuthContext";
import { CreateWorkspaceDialog } from "@/components/CreateWorkspaceDialog";
import { deleteWorkspace } from "@/api/client";
import { toast } from "sonner";

const STATUS_COLOR: Record<string, string> = {
  running: "bg-cyan", completed: "bg-green", done: "bg-green",
  failed: "bg-red", killed: "bg-red", crashed: "bg-yellow",
};
const statusColor = (s: string) => STATUS_COLOR[s] ?? "bg-yellow";

export function WorkspaceSwitcher({ currentWorkspace }: { currentWorkspace?: string }) {
  const { t } = useTranslation();
  const nav = useNavigate();
  const { data, refresh } = useWorkspaces();
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const isAdmin = user?.role === "admin";
  const pinned = user?.pinned_workspace ?? null;

  const list = useMemo(() => {
    if (!q.trim()) return data;
    const s = q.toLowerCase();
    return data.filter((w) => w.name.toLowerCase().includes(s));
  }, [data, q]);

  function pick(name: string) {
    setOpen(false);
    setQ("");
    nav(`/p/${name}`);
  }

  // admin 行内删除（spec 2026-07-27：下线 WorkspaceListPage 后删除并入切换器）。
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function doDelete() {
    if (!pendingDelete) return;
    setBusy(true);
    try {
      const target = pendingDelete;
      await deleteWorkspace(target);
      toast.success(t("workspaces.deleteDialog.deleted", { ws: target }));
      setPendingDelete(null);
      await refresh();
      // 删的是 currentWorkspace → 跳 Dashboard，避免停在已删 ws 的 404 详情。
      if (target === currentWorkspace) nav("/");
    } catch (e) {
      toast.error(t("workspaces.actionFailed", { error: e instanceof Error ? e.message : String(e) }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)} aria-label={t("workspaceSwitcher.title")}>
        <ArrowLeftRight className="size-4" /> {t("workspaceSwitcher.title")}
      </Button>
      <Dialog open={open} onOpenChange={(o) => { setOpen(o); if (!o) setQ(""); }}>
        <DialogContent className="left-0 top-0 h-screen max-w-sm translate-x-0 translate-y-0 rounded-l-none rounded-r-2xl sm:left-0">
          <DialogHeader>
            <DialogTitle className="flex items-center justify-between">
              {t("workspaceSwitcher.title")}
              <button onClick={() => setOpen(false)} aria-label="close"><X className="size-4" /></button>
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Input placeholder={t("workspaceSwitcher.search")} value={q} onChange={(e) => setQ(e.target.value)} />
            <div className="max-h-[60vh] space-y-1 overflow-y-auto">
              {list.length === 0 && <p className="text-sm text-muted-foreground">{t("workspaceSwitcher.empty")}</p>}
              {list.map((w) => (
                <div
                  key={w.name}
                  role="button"
                  tabIndex={0}
                  data-current={w.name === currentWorkspace}
                  onClick={() => pick(w.name)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(w.name); }
                  }}
                  className={`flex w-full items-center gap-2.5 rounded-md border border-transparent px-3 py-2 text-left text-sm transition-colors hover:bg-accent ${
                    w.name === currentWorkspace ? "border-border bg-accent" : ""
                  }`}
                >
                  <span className={`inline-block size-2 shrink-0 rounded-full ${statusColor(w.status)}`} />
                  <span className="flex-1 truncate font-mono">{w.name}</span>
                  {w.scan_count != null && <span className="text-xs text-muted-foreground">{w.scan_count}</span>}
                  {pinned === w.name && <Pin className="size-3.5 text-primary" />}
                  {isAdmin && (
                    <Button
                      size="icon"
                      variant="ghost"
                      className="size-6 shrink-0 text-muted-foreground hover:text-destructive"
                      data-testid={`switcher-delete-${w.name}`}
                      aria-label={t("workspaceSwitcher.deleteAria", { ws: w.name })}
                      onClick={(e) => { e.stopPropagation(); setPendingDelete(w.name); }}
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  )}
                </div>
              ))}
            </div>
            {isAdmin && (
              <div className="border-t pt-2">
                <CreateWorkspaceDialog onCreated={refresh} />
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* admin 删除工作区确认 Dialog（复用 workspaces.deleteDialog 文案） */}
      <Dialog open={!!pendingDelete} onOpenChange={(o) => !o && setPendingDelete(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("workspaces.deleteDialog.deleteTitle")}</DialogTitle>
            <DialogDescription>
              {t("workspaces.deleteDialog.deleteDesc", { ws: pendingDelete })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPendingDelete(null)}>{t("common.cancel")}</Button>
            <Button variant="destructive" disabled={busy} onClick={doDelete}>{t("common.confirm")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
