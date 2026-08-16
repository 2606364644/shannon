import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { Activity, ChevronRight, Plus, RefreshCw, Waves } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Empty } from "@/components/Empty";
import { CreateWorkspaceDialog } from "@/components/CreateWorkspaceDialog";
import { listAllScans, apiGet } from "@/api/client";
import type { ScanSummary, Workspace } from "@/api/types";
import { fmtCost, currencySymbol } from "@/utils/currency";
import { fmtTime, fmtElapsed } from "@/utils/format";
import { scanSegmentLabel } from "@/routes/WorkspaceDetail/ScanProgressBadge";
import { useAsync } from "@/lib/useAsync";
import { useAuth } from "@/auth/AuthContext";
import { cn } from "@/lib/utils";

function isToday(unix: number | null | undefined): boolean {
  if (!unix) return false;
  const d = new Date(unix * 1000);
  const now = new Date();
  return d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth()
    && d.getDate() === now.getDate();
}

const isRun = (s: ScanSummary) => s.is_running || s.status === "running";

/** 发现构成：vuln_counts 按类别聚合 → Top4 + 其他（类别≠严重度，配色用同族珊瑚递减
 *  透明度，避免被误读为严重级别）。旧数据无 vuln_counts → total=0 整条隐藏。 */
function vulnComposition(scans: ScanSummary[]) {
  const by: Record<string, number> = {};
  for (const s of scans) {
    for (const [k, v] of Object.entries(s.vuln_counts ?? {})) by[k] = (by[k] ?? 0) + v;
  }
  const rows = Object.entries(by).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  return {
    top: rows.slice(0, 4),
    rest: rows.slice(4).reduce((a, [, v]) => a + v, 0),
    total: rows.reduce((a, [, v]) => a + v, 0),
  };
}

/** 磁贴状态口径（左侧色条 + 状态字）：running=cyan / failed=red / interrupted=amber /
 *  completed 仅 0 发现时给绿色 all-clear 条（完成≠异常，无发现才值得标「清」）。 */
type TileSt = "running" | "completed" | "failed" | "interrupted";
function tileSt(s: ScanSummary | undefined): TileSt | null {
  if (!s) return null;
  if (isRun(s)) return "running";
  if (s.status === "completed" || s.status === "done") return "completed";
  if (["failed", "killed", "crashed"].includes(s.status)) return "failed";
  return "interrupted";
}

/** 猎杀特效偏好（概览本地，不进全局设置）：signal 轨迹 | flow 污点流光 */
const FX_KEY = "supernova-dash-fx";
type DashFx = "signal" | "flow";

/**
 * 概览页 = 态势大屏（重设计 v2，overview-workspace-redesign-preview.html 2026-08-16）：
 * 威胁横幅（全局数字 + 构成谱带 + 运营指标 + CTA 一行装下）+ 工作区磁贴网格
 * （运行中进度直接融进磁贴）。单屏只读——扫描明细与全部操作在工作区页，
 * 两页零结构重叠（无 Hero 大卡 / 无指标条 / 无扫描表格 / 无过滤器）。
 *
 * 聚合数据源 GET /api/scans（listAllScans 注入 workspace 字段）；有扫描在跑时 10s
 * 静默轮询保持磁贴进度新鲜。
 */
export function DashboardPage() {
  const { t } = useTranslation();
  const { data, loading, error, refresh } = useAsync(listAllScans, []);
  // admin 无 ws 空态判断：拉一次用户可见 ws 列表（与 ScanNewPage 同源 /workspaces）。
  // CreateWorkspaceDialog 唯一入口在 ws 内 Switcher（/p/:ws），无 ws 时进不去 → 着陆空态补创建入口解锁。
  // wsError：拉取失败时 workspaces=[] 会误判「无 ws」→ guard 掉，退回普通空态。
  const { data: workspaces, loading: wsLoading, error: wsError } = useAsync(() => apiGet<Workspace[]>("/workspaces"), []);
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const nav = useNavigate();

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

  // 有扫描在跑时自动轮询（10s 对齐上方新鲜度 tick）：磁贴进度/状态保持新鲜；
  // tab 隐藏暂停、跑完自动停（回归安静）。
  const hasRunning = data.some(isRun);
  useEffect(() => {
    if (!hasRunning) return;
    const id = setInterval(() => { if (!document.hidden) void refresh(); }, 10_000);
    return () => clearInterval(id);
  }, [hasRunning, refresh]);

  // 全局聚合（横幅）：发现数 / 构成 / 今日完成 / 成本 / 需关注（失败+中断，非运行非完成）。
  const running = data.filter(isRun);
  const completedToday = data.filter((s) => s.status === "completed" && isToday(s.completed_at));
  const totalVulns = data.reduce((a, s) => a + (s.vuln_count ?? 0), 0);
  const totalCost = data.reduce((a, s) => a + (s.total_cost_usd ?? 0), 0);
  const currency = data.find((s) => s.cost_currency)?.cost_currency;
  const wsCount = new Set(data.map((s) => s.workspace).filter(Boolean)).size;
  const attention = data.filter((s) => !isRun(s)
    && !["completed", "done"].includes(s.status)).length;
  const composition = useMemo(() => vulnComposition(data), [data]);

  // 工作区磁贴：按 workspace 分组；运行中的 ws 置顶，其余按最近活动倒序。
  const tiles = useMemo(() => {
    const by = new Map<string, ScanSummary[]>();
    for (const s of data) {
      const ws = s.workspace ?? "—";
      by.set(ws, [...(by.get(ws) ?? []), s]);
    }
    return [...by.entries()]
      .map(([name, scans]) => {
        const latest = [...scans].sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0))[0];
        return { name, scans, latest, hasRunning: scans.some(isRun) };
      })
      .sort((a, b) => (a.hasRunning === b.hasRunning
        ? (b.latest?.created_at ?? 0) - (a.latest?.created_at ?? 0)
        : a.hasRunning ? -1 : 1));
  }, [data]);

  if ((loading || wsLoading) && data.length === 0) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-28 w-full" />
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-44 w-full" />)}
        </div>
      </div>
    );
  }
  // 首次加载失败（无缓存可兜底）：错误态 + 重试。
  if (error && data.length === 0) {
    return (
      <Empty title={t("dashboard.errors.title")} hint={t("dashboard.errors.loadFailed", { error })}>
        <Button variant="cta" onClick={doRefresh}>{t("dashboard.fresh.refresh")}</Button>
      </Empty>
    );
  }
  if (data.length === 0) {
    // admin 且无任何工作区：创建入口本只在 ws 内 Switcher（/p/:ws）里，无 ws 时进不去 →
    // 在着陆空态补「新建工作区」，否则 admin 死锁（新建扫描要求先选 ws，无 ws 无法选）。
    if (isAdmin && !wsError && workspaces.length === 0) {
      return (
        <Empty title={t("dashboard.empty.title")} hint={t("dashboard.empty.noWorkspaceHint")}>
          <CreateWorkspaceDialog onCreated={(name) => nav(`/p/${name}`)} />
        </Empty>
      );
    }
    return (
      <Empty title={t("dashboard.empty.title")} hint={t("dashboard.empty.hint")}>
        <Button variant="cta" asChild><Link to="/scan/new"><Plus className="size-4" />{t("dashboard.newScan")}</Link></Button>
      </Empty>
    );
  }

  const allClear = totalVulns === 0;

  return (
    <div className="space-y-4">
      <h1 className="sr-only">{t("dashboard.title")}</h1>

      {/* ===== 威胁横幅：全局威胁 + 构成 + 运营指标 + CTA 一行装下（替代 Hero 大卡 + 四格指标条）。
          0 发现 = 一切正常（绿色 allClear 措辞）；有扫描在跑时底部显 FX 脉冲带。 ===== */}
      <Card className="relative overflow-hidden">
        <div className={cn("flex flex-wrap items-stretch", running.length > 0 && "pb-12")}>
          <div className="min-w-[240px] px-6 pt-4">
            <div className="flex items-center gap-2.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
              <span className="h-[13px] w-[3px] rounded-sm bg-primary" aria-hidden />
              {allClear ? t("dashboard.hero.allClearEyebrow") : t("dashboard.hero.eyebrow")}
            </div>
            <div className="mt-2 flex flex-wrap items-baseline gap-3">
              <span
                data-testid="dash-total-vulns"
                className={cn(
                  "font-mono text-[46px] font-semibold leading-none tracking-tight tabular-nums",
                  allClear ? "text-green" : "text-red",
                )}
              >
                {totalVulns.toLocaleString()}
              </span>
              <span className="whitespace-nowrap rounded-full border border-border px-2 py-0.5 text-[11.5px] text-muted-foreground">
                {allClear ? t("dashboard.hero.allClearUnit") : t("dashboard.hero.unit")}
              </span>
            </div>
          </div>

          <div className="flex min-w-[260px] flex-1 flex-col justify-center gap-2 py-4 pr-6 pl-2">
            {composition.total > 0 && (
              <div className="max-w-[460px]">
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
            <div className="font-mono text-[11.5px] text-muted-foreground">
              {t("dashboard.hero.context", {
                scans: data.length.toLocaleString(),
                workspaces: wsCount,
                live: running.length,
              })}
            </div>
          </div>

          {/* 运营指标（inline 竖切）：运行中染 cyan，需关注 >0 染 amber */}
          <div className="flex flex-wrap items-stretch border-l border-border">
            <BnStat label={t("dashboard.stats.running")} value={running.length.toLocaleString()} tone="live" />
            <BnStat label={t("dashboard.stats.completedToday")} value={completedToday.length.toLocaleString()} />
            <BnStat label={t("dashboard.stats.totalCost")} value={fmtCost(totalCost, currency)} />
            <BnStat label={t("dashboard.stats.needsAttention")} value={attention.toLocaleString()} tone={attention > 0 ? "warn" : undefined} />
          </div>

          <div className="flex items-center px-5 py-4">
            <Button variant="cta" asChild className="shrink-0">
              <Link to="/scan/new"><Plus className="size-4" />{t("dashboard.newScan")}</Link>
            </Button>
          </div>
        </div>

        {/* 猎杀特效带：仅「有扫描在跑」时贴横幅底部（一切正常=隐藏，回归安静）。
            signal=信号轨迹 / flow=污点流光，切换持久化到 localStorage。 */}
        {running.length > 0 && (
          <div className="absolute inset-x-0 bottom-0 border-t border-border/60">
            {fx === "signal" ? <SignalTrace /> : <TaintFlow />}
            <div className="flex items-center justify-end gap-1 px-4 pb-1 pt-0.5">
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

      {/* 小节标题 + 数据新鲜度（手动刷新入口；相对文案由 10s tick 驱动）+ 工作区管理入口 */}
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.1em] text-muted-foreground">
          {running.length > 0 && <span className="dash-live-dot" aria-hidden />}{t("dashboard.tiles.title")}
        </h2>
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-2.5 font-mono text-[11px] text-muted-foreground">
            {/* 轮询/手动刷新失败但仍有缓存数据：destructive 内联提示替代「N 秒前更新」 */}
            {error ? (
              <span className="text-destructive">{t("dashboard.errors.stale")}</span>
            ) : (
              <span>{ageSec < 5 ? t("dashboard.fresh.justNow") : t("dashboard.fresh.ago", { sec: ageSec })}</span>
            )}
            <Button size="sm" variant="ghost" className="h-6 gap-1 px-1.5 font-mono text-[11px]" onClick={doRefresh}>
              <RefreshCw className="h-3 w-3" />{t("dashboard.fresh.refresh")}
            </Button>
          </div>
          <Link to="/workspaces-entry" className="inline-flex items-center gap-0.5 text-[12.5px] text-muted-foreground transition-colors hover:text-primary">
            {t("dashboard.tiles.manageAll")} <ChevronRight className="size-3" />
          </Link>
        </div>
      </div>

      {/* 工作区磁贴：跨 ws 态势一目了然，运行中扫描的进度直接融进磁贴（单屏完成，无滚动长列表） */}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {tiles.map((tile) => <WsTile key={tile.name} {...tile} />)}
      </div>
    </div>
  );
}

/** 横幅运营指标格：竖切边线 + mono 数字 + 小号大写标签 */
function BnStat({ label, value, tone }: { label: string; value: ReactNode; tone?: "live" | "warn" }) {
  return (
    <div className="flex min-w-[86px] flex-col justify-center border-l border-border px-[18px] py-3 first:border-l-0">
      <div className={cn(
        "font-mono text-xl font-semibold leading-none tabular-nums",
        tone === "live" ? "text-cyan" : tone === "warn" ? "text-amber" : "text-foreground",
      )}>
        {value}
      </div>
      <div className="mt-1 text-[10px] font-medium uppercase tracking-[0.07em] text-muted-foreground">{label}</div>
    </div>
  );
}

/** 工作区磁贴：ws 名 + 状态 + 发现数/构成谱带 + 运行中扫描 mini 进度行 + meta。
 *  整卡可点进工作区页；运行中 mini 行可点直达该扫描 live（stopPropagation）。 */
function WsTile({ name, scans, latest }: { name: string; scans: ScanSummary[]; latest?: ScanSummary }) {
  const { t } = useTranslation();
  const nav = useNavigate();
  const running = scans.filter(isRun);
  const totalVulns = scans.reduce((a, s) => a + (s.vuln_count ?? 0), 0);
  const composition = useMemo(() => vulnComposition(scans), [scans]);

  const st = tileSt(latest);
  const label = st ? t(`workspaces.status.${st === "completed" ? "completed" : st}`) : "";
  // 左侧状态色条：running=cyan 辉光 / failed=red / interrupted=amber / completed+0发现=green all-clear。
  const rail =
    st === "running" ? "bg-cyan shadow-[0_0_8px_hsl(var(--c-cyan)/0.5)]"
    : st === "failed" ? "bg-red"
    : st === "interrupted" ? "bg-amber"
    : st === "completed" && totalVulns === 0 ? "bg-green/70"
    : "bg-transparent";

  return (
    <div
      data-testid={`ws-tile-${name}`}
      onClick={() => nav(`/p/${name}`)}
      className="group relative flex cursor-pointer flex-col gap-2.5 overflow-hidden rounded-xl border bg-card p-4 pl-[18px] shadow-card transition-all hover:-translate-y-0.5 hover:border-primary/55"
    >
      <span className={cn("absolute inset-y-0 left-0 w-[3px]", rail)} aria-hidden />
      <div className="flex min-w-0 items-center gap-2">
        {st === "running" && <span className="dash-live-dot h-[7px] w-[7px]" aria-hidden />}
        <span className="truncate font-mono text-[13.5px] font-semibold">{name}</span>
        <span className={cn(
          "ml-auto flex shrink-0 items-center gap-1.5 text-[10.5px] font-medium",
          st === "running" ? "text-cyan" : st === "failed" ? "text-red" : st === "interrupted" ? "text-amber" : "text-muted-foreground",
        )}>
          <span className="size-1.5 rounded-full bg-current" aria-hidden />
          {label}
        </span>
      </div>

      <div className="flex items-baseline gap-2">
        <span className={cn(
          "font-mono text-[27px] font-semibold leading-none tracking-tight tabular-nums",
          totalVulns > 0 ? "text-red" : "text-muted-foreground/70",
        )}>
          {totalVulns.toLocaleString()}
        </span>
        <span className="text-[10.5px] uppercase tracking-[0.05em] text-muted-foreground">{t("dashboard.tiles.findings")}</span>
      </div>

      {composition.total > 0 ? (
        <div>
          <div className="flex h-1 gap-px overflow-hidden rounded-full bg-border">
            {composition.top.map(([cls, n], i) => (
              <span key={cls} className="bg-primary" style={{ width: `${(n / composition.total) * 100}%`, opacity: 1 - i * 0.22 }} />
            ))}
            {composition.rest > 0 && <span className="bg-primary/25" style={{ width: `${(composition.rest / composition.total) * 100}%` }} />}
          </div>
          <div className="mt-1 truncate font-mono text-[10.5px] text-muted-foreground/90">
            {composition.top.map(([cls, n]) => `${cls} ${n.toLocaleString()}`).join(" · ")}
            {composition.rest > 0 ? ` · ${t("dashboard.hero.other")} ${composition.rest.toLocaleString()}` : ""}
          </div>
        </div>
      ) : totalVulns === 0 ? (
        <div className="font-mono text-[10.5px] text-green/80">all clear · {t("dashboard.tiles.allClear")}</div>
      ) : null}

      {/* 运行中扫描 mini 行（可点直达实时） */}
      {running.length > 0 && (
        <div className="flex flex-col gap-1.5 border-t border-dashed border-border pt-2">
          {running.map((s) => {
            const pct = Math.max(0, Math.min(100, Math.round(s.progress_pct ?? 0)));
            return (
              <Link
                key={s.scan_id}
                to={`/p/${name}/scans/${s.scan_id}/live`}
                onClick={(e) => e.stopPropagation()}
                className="grid gap-1 font-mono text-[11.5px]"
              >
                <span className="flex items-center justify-between gap-2.5">
                  <span className="truncate text-foreground transition-colors hover:text-primary">{s.workflow_id ?? s.scan_id}</span>
                  <span data-testid={`tile-run-meta-${s.scan_id}`} className="shrink-0 text-muted-foreground">
                    <span>{pct}%</span> · {scanSegmentLabel(s, null, t)} · {fmtElapsed(s.created_at)}
                  </span>
                </span>
                <span className="block h-[3px] overflow-hidden rounded-full bg-border">
                  <span className="block h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
                </span>
              </Link>
            );
          })}
        </div>
      )}

      <div className="mt-auto flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground">
        <span>{t("dashboard.tiles.scansUnit", { n: scans.length })}</span>
        <span className="opacity-40">·</span>
        <span>{tileCost(scans)}</span>
        <span className="opacity-40">·</span>
        <span>{fmtTime(latest?.created_at)}</span>
        <ChevronRight className="ml-auto size-3 shrink-0 text-muted-foreground/70 transition-all group-hover:translate-x-0.5 group-hover:text-primary" aria-hidden />
      </div>
    </div>
  );
}

/** 磁贴 meta 花费：单币种两位小数；多币种紧凑「¥102 + $18」（避免异币种数值相加成错值）。 */
function tileCost(scans: ScanSummary[]): string {
  const by = new Map<string, number>();
  for (const s of scans) {
    if (s.total_cost_usd == null) continue;
    const cur = s.cost_currency ?? "USD";
    by.set(cur, (by.get(cur) ?? 0) + s.total_cost_usd);
  }
  const entries = [...by.entries()];
  if (!entries.length) return "—";
  if (entries.length === 1) return fmtCost(entries[0][1], entries[0][0]);
  return entries.map(([cur, v]) => `${currencySymbol(cur)}${Math.round(v)}`).join(" + ");
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
