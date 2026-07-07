import { useEffect, useState, useCallback, useRef } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { listRepos, deleteRepo, pullRepo, ApiError } from "@/api/client";
import type { Repo } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { AddRepoDialog } from "@/components/AddRepoDialog";
import { CloneProgress } from "@/components/CloneProgress";

const PULL_REFRESH_DELAY_MS = 1500;
const UNGROUPED = "未分组";

function fmtSize(b?: number) {
  if (!b) return "-";
  if (b > 1_000_000) return `${(b / 1_000_000).toFixed(1)} MB`;
  if (b > 1000) return `${(b / 1000).toFixed(0)} KB`;
  return `${b} B`;
}

interface Group { name: string; repos: Repo[]; }

function groupRepos(repos: Repo[]): Group[] {
  const map = new Map<string, Repo[]>();
  for (const r of repos) {
    const g = r.group ?? UNGROUPED;
    let arr = map.get(g);
    if (!arr) { arr = []; map.set(g, arr); }
    arr.push(r);
  }
  return Array.from(map, ([name, rs]) => ({ name, repos: rs }));
}

export function ReposPage() {
  const [repos, setRepos] = useState<Repo[]>([]);
  const [loading, setLoading] = useState(true);
  const [addOpen, setAddOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const pullTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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
  useEffect(() => () => { if (pullTimerRef.current) clearTimeout(pullTimerRef.current); }, []);

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
      if (pullTimerRef.current) clearTimeout(pullTimerRef.current);
      pullTimerRef.current = setTimeout(() => void refresh(), PULL_REFRESH_DELAY_MS);
    } catch (e) {
      if (e instanceof ApiError) toast.error(`更新失败（${e.status}）`);
    }
  }

  function toggleGroup(g: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(g)) next.delete(g); else next.add(g);
      return next;
    });
  }

  const groups = groupRepos(repos);

  function renderRow(r: Repo) {
    return (
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
          <Button size="sm" variant="ghost" aria-label={`更新 ${r.name}`} onClick={() => doPull(r.name)}>更新</Button>
          <Button size="sm" variant="ghost" className="text-red" aria-label={`删除 ${r.name}`} onClick={() => setPendingDelete(r.name)}>删除</Button>
        </td>
      </tr>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="font-semibold tracking-tight text-lg">仓库</h1>
        <Button onClick={() => setAddOpen(true)}>+ 添加仓库</Button>
      </div>
      {loading ? (
        <div className="text-sm text-muted-foreground">加载中…</div>
      ) : repos.length === 0 ? (
        <div className="text-sm text-muted-foreground">暂无仓库。点「+ 添加仓库」clone 一个。</div>
      ) : (
        <div className="space-y-3">
          {groups.map((g) => {
            const isCollapsed = collapsed.has(g.name);
            return (
              <div key={g.name} className="border border-border bg-card">
                <button
                  type="button"
                  onClick={() => toggleGroup(g.name)}
                  aria-expanded={!isCollapsed}
                  className="flex w-full items-center justify-between px-4 py-2 text-left hover:bg-muted/30"
                >
                  <span className="font-medium">{g.name} <span className="text-xs text-muted-foreground">({g.repos.length})</span></span>
                  <span className="text-xs text-muted-foreground">{isCollapsed ? "展开 ▸" : "折叠 ▾"}</span>
                </button>
                {!isCollapsed && (
                  <table className="w-full text-sm">
                    <thead className="border-t border-border text-left text-muted-foreground">
                      <tr>
                        <th className="py-2 px-4">名称</th>
                        <th className="py-2 pr-4">来源</th>
                        <th className="py-2 pr-4">分支</th>
                        <th className="py-2 pr-4">大小</th>
                        <th className="py-2 pr-4">状态</th>
                        <th className="py-2 pr-4">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {g.repos.map(renderRow)}
                    </tbody>
                  </table>
                )}
              </div>
            );
          })}
        </div>
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
