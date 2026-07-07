import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { listRepos, deleteRepo, pullRepo, ApiError } from "@/api/client";
import type { Repo } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { AddRepoDialog } from "@/components/AddRepoDialog";
import { CloneProgress } from "@/components/CloneProgress";

function fmtSize(b?: number) {
  if (!b) return "-";
  if (b > 1_000_000) return `${(b / 1_000_000).toFixed(1)} MB`;
  if (b > 1000) return `${(b / 1000).toFixed(0)} KB`;
  return `${b} B`;
}

export function ReposPage() {
  const [repos, setRepos] = useState<Repo[]>([]);
  const [loading, setLoading] = useState(true);
  const [addOpen, setAddOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setRepos(await listRepos());
    } catch (e) {
      if (e instanceof ApiError) toast.error("加载仓库列表失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  async function doDelete() {
    if (!pendingDelete) return;
    try {
      setBusy(true);
      await deleteRepo(pendingDelete);
      await refresh();
    } catch (e) {
      if (e instanceof ApiError) toast.error(e.status === 409 ? "仓库正被使用" : `删除失败（${e.status}）`);
    } finally {
      setBusy(false);
      setPendingDelete(null);
    }
  }

  async function doPull(name: string) {
    try {
      await pullRepo(name);
      toast.success(`正在更新 ${name}`);
      setTimeout(() => void refresh(), 1500);
    } catch (e) {
      if (e instanceof ApiError) toast.error(`更新失败（${e.status}）`);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="font-serif text-lg">仓库</h1>
        <Button onClick={() => setAddOpen(true)}>+ 添加仓库</Button>
      </div>
      {loading ? (
        <div className="text-sm text-muted-foreground">加载中…</div>
      ) : repos.length === 0 ? (
        <div className="text-sm text-muted-foreground">暂无仓库。点「+ 添加仓库」clone 一个。</div>
      ) : (
        <table className="w-full text-sm">
          <thead className="border-b border-border text-left text-muted-foreground">
            <tr>
              <th className="py-2 pr-4">名称</th>
              <th className="py-2 pr-4">来源</th>
              <th className="py-2 pr-4">分支</th>
              <th className="py-2 pr-4">大小</th>
              <th className="py-2 pr-4">状态</th>
              <th className="py-2 pr-4">操作</th>
            </tr>
          </thead>
          <tbody>
            {repos.map((r) => (
              <tr key={r.name} className="border-b border-border">
                <td className="py-2 pr-4"><Link to={`/repos/${r.name}`} className="text-primary hover:underline">{r.name}</Link></td>
                <td className="py-2 pr-4 text-muted-foreground">{r.source?.url ?? r.source?.kind ?? "-"}</td>
                <td className="py-2 pr-4 text-muted-foreground">{r.source?.branch ?? "-"}</td>
                <td className="py-2 pr-4 text-muted-foreground">{fmtSize(r.size_bytes)}</td>
                <td className="py-2 pr-4">
                  {(r.state === "cloning" || r.state === "pulling") ? (
                    <CloneProgress name={r.name} />
                  ) : r.state === "failed" ? (
                    <span className="text-destructive">✗ 失败</span>
                  ) : r.state === "stale" ? (
                    <span className="text-yellow">⚠ 未完成</span>
                  ) : (
                    <span className="text-green">✓ 就绪</span>
                  )}
                </td>
                <td className="py-2 pr-4 space-x-2">
                  <Button size="sm" variant="ghost" onClick={() => doPull(r.name)}>更新</Button>
                  <Button size="sm" variant="ghost" className="text-red" onClick={() => setPendingDelete(r.name)}>删除</Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <AddRepoDialog open={addOpen} onOpenChange={setAddOpen} onCreated={() => void refresh()} />

      <Dialog open={!!pendingDelete} onOpenChange={(o) => !o && setPendingDelete(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除仓库</DialogTitle>
            <DialogDescription>删除仓库 {pendingDelete}？代码目录永久删除。</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPendingDelete(null)}>取消</Button>
            <Button variant="destructive" disabled={busy} onClick={doDelete}>确认</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
