import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { Activity, Plus, RefreshCw, Waves } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/StatusBadge";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Empty } from "@/components/Empty";
import { CreateWorkspaceDialog } from "@/components/CreateWorkspaceDialog";
import { ScanFilters, DEFAULT_SCAN_FILTERS, useScanFilters } from "@/components/ScanFilters";
import { listAllScans, cancelScan, apiGet, type CancelResult } from "@/api/client";
import type { ScanSummary, Workspace } from "@/api/types";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fmtCost } from "@/utils/currency";
import { useAsync } from "@/lib/useAsync";
import { useAuth } from "@/auth/AuthContext";
import { toast } from "sonner";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";

function isToday(unix: number | null | undefined): boolean {
  if (!unix) return false;
  const d = new Date(unix * 1000);
  const now = new Date();
  return d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth()
    && d.getDate() === now.getDate();
}

function fmtTime(unix?: number | null): string {
  if (!unix) return "-";
  return new Date(unix * 1000).toLocaleString();
}

/** 运行时长紧凑格式：45s / 12m / 3h 20m（created_at unix 秒 → 现在） */
function fmtElapsed(unix: number): string {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - unix));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem ? `${h}h ${rem}m` : `${h}h`;
}

/** git 地址紧凑化：去协议头 / git@ 前缀 / .git 尾（表格与进行中卡片共用口径） */
function compactUrl(u: string): string {
  return u.replace(/^https?:\/\//, "").replace(/^git@/, "").replace(/\.git$/, "");
}

/** 猎杀特效偏好（概览本地，不进全局设置）：signal 轨迹 | flow 污点流光 */
const FX_KEY = "supernova-dash-fx";
type DashFx = "signal" | "flow";

export function DashboardPage() {
  const { t } = useTranslation();
  // 跨 ws 扫描聚合(IA 重设计 §3,GET /api/scans)。每项注入 workspace 字段供表格「归属工作区」列消费。
  const { data, loading, refresh } = useAsync(listAllScans, []);
  // admin 无 ws 空态判断：拉一次用户可见 ws 列表（与 ScanNewPage 同源 /workspaces）。
  // CreateWorkspaceDialog 唯一入口在 ws 内 Switcher（/p/:ws），无 ws 时进不去 → 着陆空态补创建入口解锁。
  const { data: workspaces, loading: wsLoading } = useAsync(() => apiGet<Workspace[]>("/workspaces"), []);
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const nav = useNavigate();
  // admin 操作列：取消 running scan（spec 2026-07-27，下线 WorkspaceListPage 后取消并入 Dashboard）。
  const [pending, setPending] = useState<ScanSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [filters, setFilters] = useState(DEFAULT_SCAN_FILTERS);
  const { filtered } = useScanFilters(data, filters);

  // 猎杀特效切换（localStorage 持久化）；数据新鲜度（refreshedAt + 10s tick 驱动相对文案）。
  const [fx, setFx] = useState<DashFx>(() =>
    (typeof localStorage !== "undefined" && localStorage.getItem(FX_KEY)) === "flow" ? "flow" : "signal");
  useEffect(() => { try { localStorage.setItem(FX_KEY, fx); } catch { /* 隐私模式忽略 */ } }, [fx]);
  const [refreshedAt, setRefreshedAt] = useState(() => Date.now());
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 10_000);
    return () => clearInterval(id);
  }, []);
  useEffect(() => { setRefreshedAt(Date.now()); }, [data]);
  const doRefresh = () => { setRefreshedAt(Date.now()); void refresh(); };
  const ageSec = Math.floor((Date.now() - refreshedAt) / 1000);

  const running = filtered.filter((s) => s.is_running || s.status === "running");
  const completedToday = filtered.filter((s) => s.status === "completed" && isToday(s.completed_at));
  const totalVulns = filtered.reduce((a, s) => a + (s.vuln_count ?? 0), 0);
  const totalCost = filtered.reduce((a, s) => a + (s.total_cost_usd ?? 0), 0);
  const currency = filtered.find((s) => s.cost_currency)?.cost_currency;
  const wsCount = new Set(filtered.map((s) => s.workspace).filter(Boolean)).size;

  // 发现构成：vuln_counts 按类别聚合 → Top4 + 其他（类别≠严重度，配色用同族珊瑚递减
  // 透明度，避免被误读为严重级别）。mock/旧数据无 vuln_counts → total=0 整条隐藏。
  const composition = useMemo(() => {
    const by: Record<string, number> = {};
    for (const s of filtered) {
      for (const [k, v] of Object.entries(s.vuln_counts ?? {})) by[k] = (by[k] ?? 0) + v;
    }
    const rows = Object.entries(by).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
    return {
      top: rows.slice(0, 4),
      rest: rows.slice(4).reduce((a, [, v]) => a + v, 0),
      total: rows.reduce((a, [, v]) => a + v, 0),
    };
  }, [filtered]);

  async function doCancel() {
    if (!pending || !pending.workspace) return;
    setBusy(true);
    try {
      const res: CancelResult = await cancelScan(pending.workspace, pending.scan_id);
      // via/was_dead 区分 toast（对齐旧 WorkspaceListPage 语义，复用 workspaces.* 文案）
      if (res?.was_dead) toast.success(t("workspaces.cancelWasDead"));
      else toast.success(t("workspaces.cancelViaSignal"));
      setPending(null);
      refresh();
    } catch (e) {
      toast.error(t("workspaces.actionFailed", { error: e instanceof Error ? e.message : String(e) }));
    } finally {
      setBusy(false);
    }
  }

  if ((loading || wsLoading) && data.length === 0) {
    return (
      <div className="space-y-2">
        {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-8 w-full" />)}
      </div>
    );
  }
  if (data.length === 0) {
    // admin 且无任何工作区：创建入口本只在 ws 内 Switcher（/p/:ws）里，无 ws 时进不去 →
    // 在着陆空态补「新建工作区」，否则 admin 死锁（新建扫描要求先选 ws，无 ws 无法选）。
    if (isAdmin && workspaces.length === 0) {
      return (
        <Empty title={t("dashboard.empty.title")} hint={t("dashboard.empty.noWorkspaceHint")}>
          <CreateWorkspaceDialog onCreated={(name) => nav(`/p/${name}`)} />
        </Empty>
      );
    }
    return (
      <Empty title={t("dashboard.empty.title")} hint={t("dashboard.empty.hint")}>
        <Button variant="cta" asChild><Link to="/scan/new">{t("dashboard.newScan")}</Link></Button>
      </Empty>
    );
  }

  const allClear = totalVulns === 0;

  return (
    <div className="space-y-4">
      {/* ===== Hero（威胁信号带，替代原 PageHeader）：发现数是安全工具的头条 =====
          大号 mono 数字 + source→sink 眉标 + 发现构成谱带 + 上下文行；CTA 收右上。
          0 发现 = 一切正常（绿色 allClear 措辞，不是凄凉的空）。 */}
      <Card className="relative overflow-hidden p-6 pb-0">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="sr-only">{t("dashboard.title")}</h1>
            <div className="flex items-center gap-2.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
              <span className="h-[13px] w-[3px] rounded-sm bg-primary" aria-hidden />
              <SinkGlyph className="text-muted-foreground" />
              <span>{allClear ? t("dashboard.hero.allClearEyebrow") : t("dashboard.hero.eyebrow")}</span>
            </div>
            <div className="mt-3.5 flex flex-wrap items-baseline gap-3.5">
              <span
                className={cn(
                  "font-mono text-6xl font-semibold leading-none tracking-tight tabular-nums",
                  allClear ? "text-green" : "text-red",
                )}
              >
                {totalVulns.toLocaleString()}
              </span>
              <span className="rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground">
                {allClear ? t("dashboard.hero.allClearUnit") : t("dashboard.hero.unit")}
              </span>
            </div>
            {composition.total > 0 && (
              <div className="mt-3 max-w-[420px]">
                <div className="flex h-1.5 gap-px overflow-hidden rounded-full bg-border" aria-label={t("dashboard.hero.composition")}>
                  {composition.top.map(([cls, n], i) => (
                    <span key={cls} className="bg-primary" style={{ width: `${(n / composition.total) * 100}%`, opacity: 1 - i * 0.22 }} />
                  ))}
                  {composition.rest > 0 && <span className="bg-primary/25" style={{ width: `${(composition.rest / composition.total) * 100}%` }} />}
                </div>
                <div className="mt-1.5 flex flex-wrap gap-x-3.5 gap-y-1 font-mono text-[11px] text-muted-foreground">
                  {composition.top.map(([cls, n]) => <span key={cls}>{cls} {n.toLocaleString()}</span>)}
                  {composition.rest > 0 && <span>{t("dashboard.hero.other")} {composition.rest.toLocaleString()}</span>}
                </div>
              </div>
            )}
            <div className="mb-4 mt-3 font-mono text-xs text-muted-foreground">
              {t("dashboard.hero.context", {
                scans: filtered.length.toLocaleString(),
                workspaces: wsCount,
                live: running.length,
              })}
            </div>
          </div>
          <Button variant="cta" asChild className="shrink-0">
            <Link to="/scan/new"><Plus className="h-4 w-4" />{t("dashboard.newScan")}</Link>
          </Button>
        </div>
        {/* 猎杀特效带：仅「有扫描在跑」时渲染（一切正常=隐藏，回归安静）。
            signal=信号轨迹 / flow=污点流光，切换持久化到 localStorage。 */}
        {running.length > 0 && (
          <div className="-mx-6 mt-1 border-t border-border/60">
            {fx === "signal" ? <SignalTrace /> : <TaintFlow />}
            <div className="flex items-center justify-end gap-1 px-4 pb-1.5 pt-0.5">
              <span className="mr-1 text-[10px] uppercase tracking-wider text-muted-foreground/70">{t("dashboard.fx.toggle")}</span>
              {(["signal", "flow"] as const).map((f) => (
                <Button
                  key={f}
                  size="icon-sm"
                  variant="ghost"
                  aria-pressed={fx === f}
                  title={t(`dashboard.fx.${f}`)}
                  aria-label={t(`dashboard.fx.${f}`)}
                  className={cn("h-6 w-6", fx === f && "text-primary")}
                  onClick={() => setFx(f)}
                >
                  {f === "signal" ? <Activity className="h-3.5 w-3.5" /> : <Waves className="h-3.5 w-3.5" />}
                </Button>
              ))}
            </div>
          </div>
        )}
      </Card>

      {/* 运营指标条（安静衬底）：运行中染主色，其余中性；发现数已在 Hero 表达不重复 */}
      <Card className="grid grid-cols-2 sm:grid-cols-4 sm:divide-x sm:divide-border">
        <StripStat label={t("dashboard.stats.running")} value={running.length.toLocaleString()} accent />
        <StripStat label={t("dashboard.stats.completedToday")} value={completedToday.length.toLocaleString()} />
        <StripStat label={t("dashboard.stats.totalCost")} value={fmtCost(totalCost, currency)} />
        <StripStat label={t("dashboard.stats.totalScans")} value={filtered.length.toLocaleString()} />
      </Card>

      {running.length > 0 && (
        <section className="space-y-2">
          <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.1em] text-muted-foreground">
            <span className="dash-live-dot" aria-hidden />{t("dashboard.runningTitle")}
          </h2>
          <div className="grid gap-3 md:grid-cols-2">
            {running.map((s: ScanSummary) => {
              const pct = Math.round(s.progress_pct ?? 0);
              return (
                <Link key={s.scan_id} to={`/p/${s.workspace}/scans/${s.scan_id}/live`} className="block">
                  <Card className="transition-colors hover:border-primary">
                    <CardContent className="space-y-1.5 p-4 font-mono text-sm">
                      <div className="flex items-center gap-2.5">
                        <span className="dash-live-dot" aria-hidden />
                        <span className="truncate text-[15px] font-medium">{s.workflow_id ?? s.scan_id}</span>
                        <Badge variant="outline" className="ml-auto shrink-0 text-[10px] uppercase">{s.scan_type}</Badge>
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {t("dashboard.scanTable.workspace")}: {s.workspace} / {s.repo ?? "—"} · {t("dashboard.elapsed", { dur: fmtElapsed(s.created_at) })}
                      </div>
                      <div className="flex items-center gap-2.5">
                        <span className="h-1 min-w-[48px] flex-1 overflow-hidden rounded-full bg-border">
                          <span className="block h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
                        </span>
                        <span className="text-[11px] text-muted-foreground">{pct}%</span>
                      </div>
                      <div className="text-xs font-medium text-primary">{t("dashboard.viewLive")}</div>
                    </CardContent>
                  </Card>
                </Link>
              );
            })}
          </div>
        </section>
      )}

      <ScanFilters value={filters} onChange={setFilters} />

      {/* 小节标题 + 数据新鲜度（手动刷新入口；相对文案由 10s tick 驱动） */}
      <div className="flex items-baseline justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-[0.1em] text-muted-foreground">{t("dashboard.recentTitle")}</h2>
        <div className="flex items-center gap-2.5 font-mono text-[11px] text-muted-foreground">
          <span>{ageSec < 5 ? t("dashboard.fresh.justNow") : t("dashboard.fresh.ago", { sec: ageSec })}</span>
          <Button size="sm" variant="ghost" className="h-6 gap-1 px-1.5 font-mono text-[11px]" onClick={doRefresh}>
            <RefreshCw className="h-3 w-3" />{t("dashboard.fresh.refresh")}
          </Button>
        </div>
      </div>

      {/* 经典网格表：colgroup 收窄定性列，扫描/工作区吃余量；数字右对齐；超长省略。
          仓库列两行格（名 + 紧凑地址，悬停完整 URL）；进度列仅运行行有内容。 */}
      <Card className="overflow-hidden p-0">
        <Table>
          <colgroup>
            <col style={{ width: 112 }} /><col /><col />
            <col style={{ width: 170 }} /><col style={{ width: 116 }} /><col style={{ width: 150 }} />
            <col style={{ width: 76 }} /><col style={{ width: 100 }} /><col style={{ width: 140 }} />
            {isAdmin && <col style={{ width: 72 }} />}
          </colgroup>
          <TableHeader>
            <TableRow>
              <TableHead className="whitespace-nowrap pl-4 text-[11px] font-semibold uppercase tracking-wider">{t("dashboard.scanTable.status")}</TableHead>
              <TableHead className="whitespace-nowrap text-[11px] font-semibold uppercase tracking-wider">{t("dashboard.scanTable.scanId")}</TableHead>
              <TableHead className="whitespace-nowrap text-[11px] font-semibold uppercase tracking-wider">{t("dashboard.scanTable.workspace")}</TableHead>
              <TableHead className="whitespace-nowrap text-[11px] font-semibold uppercase tracking-wider">{t("dashboard.scanTable.repo")}</TableHead>
              <TableHead className="whitespace-nowrap text-[11px] font-semibold uppercase tracking-wider">{t("dashboard.scanTable.type")}</TableHead>
              <TableHead className="whitespace-nowrap text-[11px] font-semibold uppercase tracking-wider">{t("dashboard.scanTable.progress")}</TableHead>
              <TableHead className="whitespace-nowrap text-right text-[11px] font-semibold uppercase tracking-wider">{t("dashboard.scanTable.vulns")}</TableHead>
              <TableHead className="whitespace-nowrap text-right text-[11px] font-semibold uppercase tracking-wider">{t("dashboard.scanTable.cost")}</TableHead>
              <TableHead className="whitespace-nowrap text-[11px] font-semibold uppercase tracking-wider">{t("dashboard.scanTable.time")}</TableHead>
              {isAdmin && <TableHead className="whitespace-nowrap pr-4 text-right text-[11px] font-semibold uppercase tracking-wider">{t("dashboard.scanTable.actions")}</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((s: ScanSummary) => {
              const isRun = s.is_running || s.status === "running";
              const pct = Math.round(s.progress_pct ?? 0);
              const v = s.vuln_count ?? 0;
              return (
                <TableRow key={s.scan_id}>
                  <TableCell className="pl-4"><StatusBadge status={s.status} /></TableCell>
                  <TableCell className="max-w-0 truncate font-mono">
                    <Link to={`/p/${s.workspace}/scans/${s.scan_id}`} className="text-sm font-medium hover:text-primary">{s.workflow_id ?? s.scan_id}</Link>
                  </TableCell>
                  <TableCell className="max-w-0 truncate font-mono">
                    <Link to={`/p/${s.workspace}`} className="hover:text-primary">{s.workspace}</Link>
                  </TableCell>
                  <TableCell className="max-w-0" title={s.repo_url ?? undefined}>
                    <span className="block truncate font-mono text-xs text-muted-foreground">{s.repo ?? "—"}</span>
                    {s.repo_url && (
                      <span className="block truncate font-mono text-[10.5px] text-muted-foreground/75">
                        {compactUrl(s.repo_url)}
                      </span>
                    )}
                  </TableCell>
                  <TableCell><Badge variant="outline">{s.scan_type}</Badge></TableCell>
                  <TableCell>
                    {isRun ? (
                      <div className="flex items-center gap-2.5">
                        <div className="h-1 min-w-[48px] flex-1 overflow-hidden rounded-full bg-border">
                          <div className="h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
                        </div>
                        <span className="whitespace-nowrap font-mono text-[11px] text-muted-foreground">
                          {pct}% · {fmtElapsed(s.created_at)}
                        </span>
                      </div>
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <span className={cn("font-mono text-lg font-semibold leading-none", v > 0 ? "text-red" : "text-foreground")}>
                      {v}
                    </span>
                  </TableCell>
                  <TableCell className="text-right font-mono">{s.total_cost_usd != null ? fmtCost(s.total_cost_usd, s.cost_currency) : "-"}</TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{fmtTime(s.created_at)}</TableCell>
                  {isAdmin && (
                    <TableCell className="pr-4 text-right whitespace-nowrap">
                      {isRun ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          className="text-destructive hover:bg-destructive/10"
                          data-testid={`dashboard-cancel-scan-${s.scan_id}`}
                          onClick={() => setPending(s)}
                        >
                          {t("common.cancel")}
                        </Button>
                      ) : null}
                    </TableCell>
                  )}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Card>
      {filtered.length === 0 && <p className="text-sm text-muted-foreground">{t("workspaceDetail.scans.noMatch")}</p>}

      {/* admin 取消 running scan 确认 Dialog（per-scan 文案，对齐 ScanList） */}
      <Dialog open={!!pending} onOpenChange={(o) => !o && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("workspaceDetail.scans.cancelConfirmTitle")}</DialogTitle>
            <DialogDescription>
              {t("workspaceDetail.scans.cancelConfirmDesc", { scanId: pending?.workflow_id ?? pending?.scan_id })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPending(null)}>{t("common.cancel")}</Button>
            <Button variant="destructive" disabled={busy} onClick={doCancel}>{t("common.confirm")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** 运营指标条格：mono 大数字 + 小号大写标签（对齐 Hero 的仪表盘语感） */
function StripStat({ label, value, accent = false }: { label: string; value: ReactNode; accent?: boolean }) {
  return (
    <div className="px-5 py-4">
      <div className={cn("font-mono text-2xl font-semibold leading-none tabular-nums", accent ? "text-primary" : "text-foreground")}>
        {value}
      </div>
      <div className="mt-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
    </div>
  );
}

/** source→sink 眉标图形（与 BrandMark 同语言：两描边源点 → 珊瑚汇点） */
function SinkGlyph({ className }: { className?: string }) {
  return (
    <svg width="40" height="16" viewBox="0 0 40 18" fill="none" className={className} aria-hidden>
      <circle cx="4" cy="4" r="2.6" stroke="currentColor" strokeWidth="1.2" />
      <circle cx="4" cy="14" r="2.6" stroke="currentColor" strokeWidth="1.2" />
      <path d="M6.6 5 C 12 7, 16 7, 19 9" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
      <path d="M6.6 13 C 12 11, 16 11, 19 9" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
      <path d="M21 9 L 30 9" stroke="hsl(var(--primary))" strokeWidth="1.4" strokeLinecap="round" strokeDasharray="2 2" />
      <circle cx="34" cy="9" r="3.4" fill="hsl(var(--primary))" />
    </svg>
  );
}

/** 猎杀特效 · 信号轨迹：基线 + 脉冲线周期横扫（.dash-trace-pulse，见 index.css） */
function SignalTrace() {
  const pts = "0,17 90,17 110,17 118,17 126,9 134,25 142,4 150,30 158,17 210,17 260,17 280,17 296,17 306,12 314,22 322,17 380,17 440,17 470,17 486,17 498,10 510,24 522,17 580,17 640,17 680,17 720,17 740,17 752,13 762,21 772,17 840,17 920,17 1000,17 1100,17 1200,17";
  return (
    <svg viewBox="0 0 1200 34" preserveAspectRatio="none" className="block h-[34px] w-full" aria-hidden>
      <polyline points="0,17 1200,17" fill="none" style={{ stroke: "hsl(var(--border))", strokeWidth: 1.5 }} />
      <polyline className="dash-trace-pulse" points={pts} />
    </svg>
  );
}

/** 猎杀特效 · 污点流光：三条泳道（珊瑚/青/红）数据持续向右流动（.dash-flow-lane） */
function TaintFlow() {
  return (
    <svg viewBox="0 0 1200 48" preserveAspectRatio="none" className="block h-12 w-full" aria-hidden>
      <path className="dash-flow-lane" d="M0,14 C150,8 300,20 450,14 S750,8 900,14 S1100,20 1200,14" />
      <path className="dash-flow-lane l2" d="M0,26 C200,32 400,20 600,26 S1000,32 1200,26" />
      <path className="dash-flow-lane l3" d="M0,38 C150,42 350,34 550,38 S950,42 1200,38" />
    </svg>
  );
}
