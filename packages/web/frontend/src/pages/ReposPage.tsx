import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { listRepos, deleteRepo, pullRepo, ApiError } from "@/api/client";
import type { Repo, RepoState } from "@/api/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
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

// 状态 -> 徽章文本/色（对齐 StatusBadge 的 DSF token 配色）。
// 文本须保留：✗ 失败 / ⚠ 未完成（测试断言），cloning 走 CloneProgress（含 "clone 中"）。
const STATE_BADGE: Record<RepoState, { text: string; cls: string }> = {
  ready:   { text: "✓ 就绪",   cls: "border-green/40 text-green" },
  failed:  { text: "✗ 失败",   cls: "border-red/40 text-red" },
  stale:   { text: "⚠ 未完成", cls: "border-yellow/40 text-yellow" },
  cloning: { text: "clone 中", cls: "border-cyan/40 text-cyan" },
  pulling: { text: "pull 中",  cls: "border-cyan/40 text-cyan" },
};

function StateCell({ repo }: { repo: Repo }) {
  // cloning/pulling 复用 CloneProgress（含进度条 + "clone 中" 文本，测试断言依赖）
  if (repo.state === "cloning" || repo.state === "pulling") {
    return <CloneProgress name={repo.name} />;
  }
  const m = STATE_BADGE[repo.state];
  return <Badge variant="outline" className={cn("gap-1 font-mono", m.cls)}>{m.text}</Badge>;
}

/** 截断长文本 + hover tooltip 显示完整值（修 URL/仓库名撑爆行、换行错乱的根因）。 */
function Ellipsis({ value, className }: { value: string; className?: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className={cn("block truncate", className)}>{value}</span>
      </TooltipTrigger>
      <TooltipContent className="max-w-md break-all">{value}</TooltipContent>
    </Tooltip>
  );
}

export function ReposPage() {
  const [repos, setRepos] = useState<Repo[]>([]);
  const [loading, setLoading] = useState(true);
  const [addOpen, setAddOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
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

  // 搜索：按仓库名过滤（跨分组），空分组卡片自动隐藏
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return repos;
    return repos.filter((r) => r.name.toLowerCase().includes(q));
  }, [repos, query]);

  const groups = groupRepos(filtered);

  return (
    <TooltipProvider>
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="font-semibold tracking-tight text-lg">仓库</h1>
          <div className="flex items-center gap-2">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索仓库名"
              className="w-56"
              aria-label="搜索仓库名"
            />
            <Button onClick={() => setAddOpen(true)}>+ 添加仓库</Button>
          </div>
        </div>

        {loading ? (
          <div className="text-sm text-muted-foreground">加载中…</div>
        ) : filtered.length === 0 ? (
          <div className="text-sm text-muted-foreground">
            {repos.length === 0 ? "暂无仓库。点「+ 添加仓库」clone 一个。" : "无匹配仓库。"}
          </div>
        ) : (
          <div className="space-y-3">
            {groups.map((g) => {
              const isCollapsed = collapsed.has(g.name);
              return (
                <Card key={g.name} className="overflow-hidden">
                  <button
                    type="button"
                    onClick={() => toggleGroup(g.name)}
                    aria-expanded={!isCollapsed}
                    className="flex w-full items-center justify-between px-4 py-2.5 text-left hover:bg-muted/30"
                  >
                    <span className="font-medium">
                      {g.name} <span className="text-xs text-muted-foreground">({g.repos.length})</span>
                    </span>
                    <span className="text-xs text-muted-foreground">{isCollapsed ? "展开 ▸" : "折叠 ▾"}</span>
                  </button>
                  {!isCollapsed && (
                    <Table className="table-fixed">
                      <TableHeader>
                        <TableRow className="border-t border-border hover:bg-transparent">
                          <TableHead className="w-56 py-2 pl-4 pr-3 text-muted-foreground">名称</TableHead>
                          <TableHead className="py-2 px-3 text-muted-foreground">来源</TableHead>
                          <TableHead className="w-32 py-2 px-3 text-muted-foreground">分支</TableHead>
                          <TableHead className="w-20 py-2 px-3 text-right text-muted-foreground">大小</TableHead>
                          <TableHead className="w-24 py-2 px-3 text-muted-foreground">状态</TableHead>
                          <TableHead className="w-32 py-2 pl-3 pr-4 text-right text-muted-foreground">操作</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {g.repos.map((r) => {
                          const url = r.source?.url;
                          return (
                            <TableRow key={r.name} className="border-b border-border">
                              {/* 名称：可点 + 截断（超长 group/repo hover 看全名） */}
                              <TableCell className="py-2 pl-4 pr-3">
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <Link
                                      to={`/repos/${r.name}`}
                                      className="block truncate text-primary hover:underline"
                                    >
                                      {r.name}
                                    </Link>
                                  </TooltipTrigger>
                                  <TooltipContent>{r.name}</TooltipContent>
                                </Tooltip>
                              </TableCell>
                              {/* 来源：URL 截断 + tooltip；无 URL 显 kind/- */}
                              <TableCell className="py-2 px-3">
                                {url ? (
                                  <Ellipsis value={url} className="font-mono text-xs text-muted-foreground" />
                                ) : (
                                  <span className="text-muted-foreground">{r.source?.kind ?? "-"}</span>
                                )}
                              </TableCell>
                              <TableCell className="py-2 px-3">
                                <span className="block truncate font-mono text-xs text-muted-foreground">
                                  {r.source?.branch ?? "-"}
                                </span>
                              </TableCell>
                              <TableCell className="py-2 px-3 text-right tabular-nums text-muted-foreground">
                                {fmtSize(r.size_bytes)}
                              </TableCell>
                              <TableCell className="py-2 px-3">
                                <StateCell repo={r} />
                              </TableCell>
                              <TableCell className="py-2 pl-3 pr-4 text-right">
                                <span className="inline-flex gap-1">
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    aria-label={`更新 ${r.name}`}
                                    onClick={() => doPull(r.name)}
                                  >
                                    更新
                                  </Button>
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    className="text-red"
                                    aria-label={`删除 ${r.name}`}
                                    onClick={() => setPendingDelete(r.name)}
                                  >
                                    删除
                                  </Button>
                                </span>
                              </TableCell>
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                  )}
                </Card>
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
    </TooltipProvider>
  );
}
