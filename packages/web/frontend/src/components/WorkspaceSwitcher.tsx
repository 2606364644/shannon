import { useState, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { ArrowLeftRight, X, Pin } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useWorkspaces } from "@/api/useWorkspaces";
import { useAuth } from "@/auth/AuthContext";
import { CreateWorkspaceDialog } from "@/components/CreateWorkspaceDialog";

const STATUS_COLOR: Record<string, string> = {
  running: "bg-cyan", completed: "bg-green", done: "bg-green",
  failed: "bg-red", killed: "bg-red", crashed: "bg-yellow",
};
const statusColor = (s: string) => STATUS_COLOR[s] ?? "bg-yellow";

export function WorkspaceSwitcher({ currentWorkspace }: { currentWorkspace?: string }) {
  const { t } = useTranslation();
  const nav = useNavigate();
  const { data } = useWorkspaces();
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

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)} aria-label={t("workspaceSwitcher.title")}>
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
                <button
                  key={w.name}
                  data-current={w.name === currentWorkspace}
                  onClick={() => pick(w.name)}
                  className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm hover:bg-accent ${
                    w.name === currentWorkspace ? "bg-accent" : ""
                  }`}
                >
                  <span className={`inline-block size-2 rounded-full ${statusColor(w.status)}`} />
                  <span className="flex-1 font-mono">{w.name}</span>
                  {w.scan_count != null && <span className="text-xs text-muted-foreground">{w.scan_count}</span>}
                  {pinned === w.name && <Pin className="size-3 text-primary" />}
                </button>
              ))}
            </div>
            {isAdmin && (
              <div className="border-t pt-2">
                <CreateWorkspaceDialog onCreated={() => {}} />
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
