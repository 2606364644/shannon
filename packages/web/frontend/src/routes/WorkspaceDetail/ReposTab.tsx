import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { listRepos, deleteRepo, pullRepo, ApiError } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import type { Repo, RepoState } from "@/api/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Card } from "@/components/ui/card";
import { StatRow, type StatItem } from "@/components/StatRow";
import { ChevronDown } from "lucide-react";
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

function StateBadge({ ws, repo }: { ws: string; repo: Repo }) {
  const { t } = useTranslation();
  // cloning/pulling 复用 CloneProgress（含进度条 + "clone 中" 文本）
  if (repo.state === "cloning" || repo.state === "pulling") {
    return <CloneProgress ws={ws} name={repo.name} />;
  }
  const m = STATE_BADGE[repo.state];
  return <Badge variant="outline" className={cn("gap-1 font-mono", m.cls)}>{t(m.key)}</Badge>;
}

interface Props {
  /**
   * workspace 名。两种来源：
   *  - 路由模式下：父组件不传，从 useParams() 取（router.tsx 里 `<ReposTab />` 不带 props）。
   *  - 测试 / 嵌入：直接传字面值（如 `<ReposTab workspace="ws1" />`）。
   */
  workspace?: string;
}

/**
 * 工作区内的"仓库"tab：克隆/列表/pull/checkout/删除。
 * 由原 pages/ReposPage.tsx + RepoDetailPage.tsx 合并迁入（P2：仓库迁到 ws 内）。
 *  - 不再独立 /repos 路由（TopBar 不再有其入口）。
 *  - 所有 API 调用带 ws 参数。
 *  - 仓库 → RepoDetail 的二级路由也一并撤销（无独立详情页；扫描入口在新建扫描页选 repo）。
 */
export function ReposTab({ workspace: wsProp }: Props) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const params = useParams<{ workspace: string }>();
  const workspace = wsProp ?? params.workspace ?? "";
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
      setRepos(await listRepos(workspace));
    } catch (e) {
      if (e instanceof ApiError) toast.error(t("repos.errors.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t, workspace]);

  // 等用户态 ready 再拉取（与 MemberManagerDialog 一致）。
  // 在生产环境路由已包 RequireAuth，user 进入此组件时已就绪；
  // 在测试里 AuthProvider 异步拉 /auth/me，未就绪时跳过首拉、待 user 改变再触发。
  useEffect(() => { if (user) void refresh(); }, [refresh, user]);
  useEffect(() => () => { if (pullTimerRef.current) clearTimeout(pullTimerRef.current); }, []);

  async function doDelete() {
    if (!pendingDelete) return;
    try {
      setBusy(true);
      await deleteRepo(workspace, pendingDelete);
      await refresh();
    } catch (e) {
      if (e instanceof ApiError) {
        toast.error(
          e.status === 409
            ? t("repos.errors.inUse")
            : t("repos.errors.deleteFailed", { status: e.status }),
        );
      }
    } finally {
      setBusy(false);
      setPendingDelete(null);
    }
  }

  async function doPull(name: string) {
    try {
      await pullRepo(workspace, name);
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

  // 概览条：客户端聚合 repos（不动后端契约）
  const stats: StatItem[] = useMemo(() => {
    const totalSize = repos.reduce((s, r) => s + (r.size_bytes ?? 0), 0);
    return [
      { label: t("repos.stats.total"), value: repos.length },
      { label: t("repos.stats.size"), value: fmtSize(totalSize) },
      { label: t("repos.stats.ready"), value: repos.filter((r) => r.state === "ready").length, tone: "green" },
      { label: t("repos.stats.cloning"), value: repos.filter((r) => r.state === "cloning" || r.state === "pulling").length, tone: "cyan" },
    ];
  }, [repos, t]);

  return (
    <TooltipProvider>
      <div className="space-y-4">
        <StatRow stats={stats} />
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="cta" onClick={() => setAddOpen(true)}>{t("repos.addRepo")}</Button>
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("repos.searchPlaceholder")}
            className="w-56"
            aria-label={t("repos.searchPlaceholder")}
          />
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
                <div key={g.name}>
                  <button
                    type="button"
                    onClick={() => toggleGroup(g.name)}
                    aria-expanded={!isCollapsed}
                    className="flex w-full items-center gap-2 rounded-md border border-border bg-card px-4 py-2.5 text-left"
                  >
                    <span className="font-medium">{g.name}</span>
                    <span className="text-sm text-muted-foreground">({g.repos.length})</span>
                    <ChevronDown className={cn("ml-auto h-4 w-4 text-muted-foreground transition-transform", isCollapsed && "-rotate-90")} />
                  </button>
                  {!isCollapsed && (
                    <Card className="mt-2 overflow-hidden p-0">
                    <Table className="table-fixed">
                      <TableHeader>
                        <TableRow className="border-t border-border hover:bg-transparent">
                          <TableHead className="w-56 py-2 pl-4 pr-3">{t("repos.table.name")}</TableHead>
                          <TableHead className="py-2 px-3">{t("repos.table.source")}</TableHead>
                          <TableHead className="w-32 py-2 px-3">{t("repos.table.branch")}</TableHead>
                          <TableHead className="w-24 whitespace-nowrap py-2 px-3 text-right">{t("repos.table.size")}</TableHead>
                          <TableHead className="w-36 whitespace-nowrap py-2 px-3">{t("repos.table.state")}</TableHead>
                          <TableHead className="w-36 whitespace-nowrap py-2 px-3 text-center">{t("repos.table.actions")}</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {g.repos.map((r) => {
                          const url = r.source?.url;
                          return (
                            <TableRow key={r.name} className="border-b border-border">
                              {/* 名称（不再点入 RepoDetail：P2 撤销了 /repos/* 路由） */}
                              <TableCell className="py-2 pl-4 pr-3">
                                <div className="flex items-center gap-2">
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <span className="block truncate font-mono text-sm">{r.name}</span>
                                    </TooltipTrigger>
                                    <TooltipContent>{r.name}</TooltipContent>
                                  </Tooltip>
                                  {r.linked && (
                                    <Badge variant="outline" className="border-cyan/40 text-cyan">
                                      {t("repos.linkedBadge")}
                                    </Badge>
                                  )}
                                </div>
                              </TableCell>
                              {/* 来源：URL 截断 + tooltip + 复制按钮 */}
                              <TableCell className="py-2 px-3">
                                {url ? (
                                  <div className="relative flex items-center">
                                    <Tooltip>
                                      <TooltipTrigger asChild>
                                        <span className="block w-full truncate font-mono text-sm">{url}</span>
                                      </TooltipTrigger>
                                      <TooltipContent className="max-w-md break-all">{url}</TooltipContent>
                                    </Tooltip>
                                    <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center justify-end bg-gradient-to-l from-background via-background to-transparent pl-8">
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
                                <span className="block truncate font-mono text-sm">
                                  {r.source?.branch ?? "-"}
                                </span>
                              </TableCell>
                              <TableCell className="whitespace-nowrap py-2 px-3 text-right tabular-nums">
                                {fmtSize(r.size_bytes)}
                              </TableCell>
                              <TableCell className="whitespace-nowrap py-2 px-3">
                                <StateBadge ws={workspace} repo={r} />
                              </TableCell>
                              <TableCell className="whitespace-nowrap py-2 px-3 text-center">
                                <span className="inline-flex gap-1">
                                  {/* 关联仓库只读：隐藏更新(pull)——共享路径下 pull 会跨 ws 干扰 */}
                                  {!r.linked && (
                                    <Button
                                      size="sm"
                                      variant="ghost"
                                      aria-label={t("repos.updateAria", { name: r.name })}
                                      onClick={() => doPull(r.name)}
                                    >
                                      {t("common.update")}
                                    </Button>
                                  )}
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    className="text-destructive hover:bg-destructive/10"
                                    aria-label={t("repos.deleteAria", { name: r.name })}
                                    onClick={() => setPendingDelete(r.name)}
                                  >
                                    {/* 关联仓库：取消关联（仅移除引用，不删源文件） */}
                                    {r.linked ? t("repos.unlink") : t("common.delete")}
                                  </Button>
                                </span>
                              </TableCell>
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                    </Card>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <AddRepoDialog ws={workspace} open={addOpen} onOpenChange={setAddOpen} onCreated={() => void refresh()} />

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
