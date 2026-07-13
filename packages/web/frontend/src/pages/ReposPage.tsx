import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
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
import { CopyButton } from "@/components/CopyButton";

const PULL_REFRESH_DELAY_MS = 1500;

function fmtSize(b?: number) {
  if (!b) return "-";
  if (b > 1_000_000) return `${(b / 1_000_000).toFixed(1)} MB`;
  if (b > 1000) return `${(b / 1000).toFixed(0)} KB`;
  return `${b} B`;
}

interface Group { name: string; repos: Repo[]; }

function groupRepos(repos: Repo[], ungrouped: string): Group[] {
  const map = new Map<string, Repo[]>();
  for (const r of repos) {
    const g = r.group ?? ungrouped;
    let arr = map.get(g);
    if (!arr) { arr = []; map.set(g, arr); }
    arr.push(r);
  }
  return Array.from(map, ([name, rs]) => ({ name, repos: rs }));
}

// 状态 -> 徽章 i18n key/色（对齐 StatusBadge 的 DSF token 配色）。
// 状态文本已迁移到 i18n（repos.states.*），默认中文渲染保持 ✓就绪 / ✗ 失败 / ⚠ 未完成
// （测试断言依赖默认中文渲染）；cloning/pulling 走 CloneProgress（含 "clone 中"）。
const STATE_BADGE: Record<RepoState, { key: string; cls: string }> = {
  ready:   { key: "repos.states.ready",   cls: "border-green/40 text-green" },
  failed:  { key: "repos.states.failed",  cls: "border-red/40 text-red" },
  stale:   { key: "repos.states.stale",   cls: "border-yellow/40 text-yellow" },
  cloning: { key: "repos.states.cloning", cls: "border-cyan/40 text-cyan" },
  pulling: { key: "repos.states.pulling", cls: "border-cyan/40 text-cyan" },
};

function StateCell({ repo }: { repo: Repo }) {
  const { t } = useTranslation();
  // cloning/pulling 复用 CloneProgress（含进度条 + "clone 中" 文本，测试断言依赖）
  if (repo.state === "cloning" || repo.state === "pulling") {
    return <CloneProgress name={repo.name} />;
  }
  const m = STATE_BADGE[repo.state];
  return <Badge variant="outline" className={cn("gap-1 font-mono", m.cls)}>{t(m.key)}</Badge>;
}



export function ReposPage() {
  const { t } = useTranslation();
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
      if (e instanceof ApiError) toast.error(t("repos.errors.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => () => { if (pullTimerRef.current) clearTimeout(pullTimerRef.current); }, []);

  async function doDelete() {
    if (!pendingDelete) return;
    try {
      setBusy(true);
      await deleteRepo(pendingDelete);
      await refresh();
    } catch (e) {
      if (e instanceof ApiError) toast.error(e.status === 409 ? t("repos.errors.inUse") : t("repos.errors.deleteFailed", { status: e.status }));
    } finally {
      setBusy(false);
      setPendingDelete(null);
    }
  }

  async function doPull(name: string) {
    try {
      await pullRepo(name);
      toast.success(t("repos.updating", { name }));
      if (pullTimerRef.current) clearTimeout(pullTimerRef.current);
      pullTimerRef.current = setTimeout(() => void refresh(), PULL_REFRESH_DELAY_MS);
    } catch (e) {
      if (e instanceof ApiError) toast.error(t("repos.errors.updateFailed", { status: e.status }));
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

  const groups = groupRepos(filtered, t("repos.ungrouped"));

  return (
    <TooltipProvider>
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="font-semibold tracking-tight text-lg">{t("repos.title")}</h1>
          <div className="flex items-center gap-2">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("repos.searchPlaceholder")}
              className="w-56"
              aria-label={t("repos.searchPlaceholder")}
            />
            <Button onClick={() => setAddOpen(true)}>{t("repos.addRepo")}</Button>
          </div>
        </div>

        {loading ? (
          <div className="text-sm text-muted-foreground">{t("repos.loading")}</div>
        ) : filtered.length === 0 ? (
          <div className="text-sm text-muted-foreground">
            {repos.length === 0 ? t("repos.empty") : t("repos.noMatch")}
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
                    <span className="text-xs text-muted-foreground">{isCollapsed ? t("repos.expand") : t("repos.collapse")}</span>
                  </button>
                  {!isCollapsed && (
                    <Table className="table-fixed">
                      <TableHeader>
                        <TableRow className="border-t border-border hover:bg-transparent">
                          <TableHead className="w-56 py-2 pl-4 pr-3 text-muted-foreground">{t("repos.table.name")}</TableHead>
                          <TableHead className="py-2 px-3 text-muted-foreground">{t("repos.table.source")}</TableHead>
                          <TableHead className="w-32 py-2 px-3 text-muted-foreground">{t("repos.table.branch")}</TableHead>
                          <TableHead className="w-20 py-2 px-3 text-right text-muted-foreground">{t("repos.table.size")}</TableHead>
                          <TableHead className="w-24 py-2 px-3 text-muted-foreground">{t("repos.table.state")}</TableHead>
                          <TableHead className="w-36 whitespace-nowrap py-2 pl-3 pr-4 text-right text-muted-foreground">{t("repos.table.actions")}</TableHead>
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
                              {/* 来源：URL 截断 + tooltip + 复制按钮；长 URL 被右侧渐变蒙层 + 按钮覆盖，不挤压右侧列 */}
                              <TableCell className="py-2 px-3">
                                {url ? (
                                  <div className="relative flex items-center">
                                    <Tooltip>
                                      <TooltipTrigger asChild>
                                        <span className="block w-full truncate font-mono text-xs text-muted-foreground">{url}</span>
                                      </TooltipTrigger>
                                      <TooltipContent className="max-w-md break-all">{url}</TooltipContent>
                                    </Tooltip>
                                    <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center justify-end bg-gradient-to-l from-card via-card to-transparent pl-8">
                                      <CopyButton
                                        value={url}
                                        ariaLabel={t("repos.copyUrlAria", { name: r.name })}
                                        className="pointer-events-auto"
                                      />
                                    </div>
                                  </div>
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
                              <TableCell className="whitespace-nowrap py-2 pl-3 pr-4 text-right">
                                <span className="inline-flex gap-1">
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    aria-label={t("repos.updateAria", { name: r.name })}
                                    onClick={() => doPull(r.name)}
                                  >
                                    {t("common.update")}
                                  </Button>
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    className="text-red"
                                    aria-label={t("repos.deleteAria", { name: r.name })}
                                    onClick={() => setPendingDelete(r.name)}
                                  >
                                    {t("common.delete")}
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
              <DialogTitle>{t("repos.deleteDialog.title")}</DialogTitle>
              <DialogDescription>{t("repos.deleteDialog.desc", { name: pendingDelete })}</DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="ghost" onClick={() => setPendingDelete(null)}>{t("common.cancel")}</Button>
              <Button variant="destructive" disabled={busy} onClick={doDelete}>{t("common.confirm")}</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </TooltipProvider>
  );
}
