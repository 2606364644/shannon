import { useEffect, useMemo, useState } from "react";
// useEffect/useState 仍被行内组件（SSE 订阅等）使用；列表数据层已上移 useScans。
import { useTranslation } from "react-i18next";
import { useParams, useNavigate, useOutletContext, Link } from "react-router-dom";
import { toast } from "sonner";
import { Ban, ChevronRight, Eye, Play, RefreshCw, Search, Trash2 } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { StatusBadge } from "@/components/StatusBadge";
import { ErrorState } from "@/components/ErrorState";
import { Empty } from "@/components/Empty";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
  cancelScan, deleteScan, deleteBlackboxRun, resumeScan, getScan, scanEventsUrl, ApiError,
} from "@/api/client";
import { useScans } from "./useScans";
import { useEventSource } from "@/api/useEventSource";
import type { BlackboxRunSummary, ScanSummary } from "@/api/types";
import { fmtCost } from "@/utils/currency";
import { fmtTime, fmtDur, compactUrl } from "@/utils/format";
import { isRunTerminal } from "./runStatus";
import { scanSegmentLabel } from "./ScanProgressBadge";
import type { WsOverviewCtx } from "./";

// 终态集（spec §5.1 resume 仅非终态放行，终态 422）。interrupted 等属未完成可恢复。
// cancelled 是终态（用户主动停；后端 resume 拒 422）→ 显 查看/重跑/删除 而非恢复。
const TERMINAL = new Set(["completed", "done", "failed", "killed", "crashed", "cancelled"]);

// 运行中判定（分段过滤用）；轮询节奏由 useScans 的 SWR refreshInterval 管理。
const isRun = (s: ScanSummary) => s.is_running || s.status === "running";

/** 状态分段（filter 分段控件口径）：running/completed/failed + other（interrupted 等，仅「全部」可见）。 */
type Seg = "running" | "completed" | "failed";
function segOf(s: ScanSummary): Seg | "other" {
  if (isRun(s)) return "running";
  if (s.status === "completed" || s.status === "done") return "completed";
  if (["failed", "killed", "crashed"].includes(s.status)) return "failed";
  return "other";
}

/** 筛选态：分段（全部/运行中/已完成/失败）+ 类型 + 关键词。
 *  类型模型（重设计 2026-08-16）：只有白盒 + 组合——组合扫描 scan_type 仍为 whitebox、
 *  靠 combined 标记识别；黑盒一律是组合任务的嵌套 run，无独立行/入口。
 *  "whitebox" = 纯白盒（combined !== true），"combined" = 组合。 */
type TypeFilter = "all" | "whitebox" | "combined";
interface ListFilters { seg: "all" | Seg; type: TypeFilter; keyword: string }
const DEFAULT_LIST_FILTERS: ListFilters = { seg: "all", type: "all", keyword: "" };

function matchType(s: ScanSummary, type: TypeFilter): boolean {
  if (type === "combined") return s.combined === true;
  if (type === "whitebox") return s.scan_type === "whitebox" && s.combined !== true;
  return true;
}

function fmtTimeFull(unix?: number | null): string {
  if (!unix) return "";
  return new Date(unix * 1000).toLocaleString();
}

/** 从 SSE events 推当前阶段（最后一条 PhaseEvent(start).phase）；无则 null。
 *  列表行粗粒度用：纯白盒/纯黑盒段标签后缀（如「白盒 · recon」）。 */
function useCurrentPhase(events: { type: string; event?: string; phase?: string }[]): string | null {
  return useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      if (e.type === "PhaseEvent" && e.event === "start") return e.phase ?? null;
    }
    return null;
  }, [events]);
}

export function ScanList() {
  const { t } = useTranslation();
  const { workspace } = useParams<{ workspace: string }>();
  // WorkspaceDetail（Outlet 父级）聚合联动：操作/scan_end 后同步刷新 Hero/指标条。
  // 独立渲染（单测直挂 Route）时无 context → null，退化为仅自身刷新。
  const wsCtx = useOutletContext<WsOverviewCtx | null>();
  // SWR 数据层（spec §6.3）：与父容器共享 key → 单请求单轮询（运行中才 10s，后台 tab 暂停）。
  const { scans, loading, error: err, refresh: refreshScans } = useScans(workspace);
  const [filters, setFilters] = useState<ListFilters>(DEFAULT_LIST_FILTERS);

  // 关键词 + 类型先行过滤（分段计数以此为准，计数不随当前分段变化）；
  // 分段口径见 segOf：other（interrupted 等）只在「全部」出现。
  // 列表量小（单 ws 扫描数）直算即可，避免在 err 早退后引入条件 hook。
  const kwTyped = scans.filter((s) => {
    if (!matchType(s, filters.type)) return false;
    const q = filters.keyword.trim().toLowerCase();
    if (q) {
      const hay = `${s.workflow_id ?? ""} ${s.scan_id} ${s.repo ?? ""} ${s.repo_url ?? ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  const segCounts = {
    all: kwTyped.length,
    running: kwTyped.filter((s) => segOf(s) === "running").length,
    completed: kwTyped.filter((s) => segOf(s) === "completed").length,
    failed: kwTyped.filter((s) => segOf(s) === "failed").length,
  };

  if (err) return <ErrorState message={t("workspaceDetail.scans.loadError", { error: err })} />;

  const filtered = filters.seg === "all" ? kwTyped : kwTyped.filter((s) => segOf(s) === filters.seg);

  // 行操作后：刷新自身列表 + 联动 Hero 聚合（同 key mutate 经 SWR 去重为一次请求）。 */
  const reload = () => { refreshScans(); wsCtx?.refresh?.(); };

  // 空工作区（v4）：过滤器隐藏（无对象可过滤）、列表头 CTA 移除（空态卡是唯一主操作）。
  // loading 期间保持显（避免加载闪隐），加载完确认为空才收起。
  const showListChrome = loading || scans.length > 0;

  // 耗时分布（标题行运营摘要）：平均/最长/最短（仅有 total_duration_ms 的扫描参与）。
  const durations = scans.map((s) => s.total_duration_ms ?? 0).filter((ms) => ms > 0);
  const avgInfo = durations.length
    ? {
        avg: fmtDur(durations.reduce((a, b) => a + b, 0) / durations.length),
        max: fmtDur(Math.max(...durations)),
        min: fmtDur(Math.min(...durations)),
      }
    : null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-baseline gap-x-2.5">
          <h3 className="text-base font-semibold tracking-tight">{t("workspaceDetail.scans.listTitle")}</h3>
          {showListChrome && (
            <>
              <span className="font-mono text-sm font-semibold tabular-nums">{scans.length}</span>
              {avgInfo && (
                <span className="font-mono text-[11.5px] text-muted-foreground">
                  {t("workspaceDetail.scans.avgCtx", avgInfo)}
                </span>
              )}
            </>
          )}
        </div>
        {workspace && showListChrome && (
          <Button variant="cta" asChild>
            <Link to={`/scan/new?workspace=${encodeURIComponent(workspace)}`}>
              {t("workspaceDetail.scans.newScan")}
            </Link>
          </Button>
        )}
      </div>

      {/* 过滤器（预览 2026-08-15）：搜索 + 状态分段（带计数）+ 类型 select；空工作区隐藏 */}
      {showListChrome && (
        <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[200px] flex-1 sm:max-w-xs">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            value={filters.keyword}
            onChange={(e) => setFilters((f) => ({ ...f, keyword: e.target.value }))}
            placeholder={t("workspaceDetail.scans.searchPlaceholder")}
            aria-label={t("workspaceDetail.scans.searchPlaceholder")}
            className="pl-9"
          />
        </div>
        <div className="inline-flex rounded-[10px] bg-muted p-[3px]" role="group" aria-label={t("scanFilters.status")}>
          {([["all", "workspaceDetail.scans.seg.all", segCounts.all],
             ["running", "workspaces.status.running", segCounts.running],
             ["completed", "workspaces.status.completed", segCounts.completed],
             ["failed", "workspaces.status.failed", segCounts.failed]] as const).map(([seg, key, n]) => (
            <button
              key={seg}
              type="button"
              aria-pressed={filters.seg === seg}
              onClick={() => setFilters((f) => ({ ...f, seg }))}
              className={`rounded-md px-3 py-1 text-[12.5px] font-medium transition-colors ${
                filters.seg === seg
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {t(key)}<span className="ml-1 font-mono text-[11px] opacity-70">{n}</span>
            </button>
          ))}
        </div>
        {/* 类型模型收窄（2026-08-16）：只有白盒 + 组合——黑盒是组合任务的嵌套 run，
            无独立类型可选；correlation 后端未实现，不入选项 */}
        <Select value={filters.type} onValueChange={(v) => setFilters((f) => ({ ...f, type: v as TypeFilter }))}>
          <SelectTrigger aria-label={t("scanFilters.type")} className="w-40"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("workspaces.filter.allType")}</SelectItem>
            <SelectItem value="whitebox">{t("workspaces.filter.whitebox")}</SelectItem>
            <SelectItem value="combined">{t("workspaceDetail.scans.typeFilterCombined")}</SelectItem>
          </SelectContent>
        </Select>
        </div>
      )}

      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-16 w-full" />)}
        </div>
      ) : scans.length === 0 ? (
        /* 空态卡（v4）：唯一 CTA + 次级入口（对应命令栏的仓库/认证，降低首次使用门槛） */
        <Empty title={t("workspaceDetail.scans.empty")} hint={t("workspaceDetail.scans.emptyHint")}>
          <div className="flex flex-col items-center gap-3">
            {workspace && (
              <Button variant="cta" asChild>
                <Link to={`/scan/new?workspace=${encodeURIComponent(workspace)}`}>
                  {t("workspaceDetail.scans.newScan")}
                </Link>
              </Button>
            )}
            <div className="flex gap-3.5 text-xs">
              <Link to="repos" className="text-primary hover:underline">
                {t("workspaceDetail.scans.setupRepos")}
              </Link>
              <Link to="auth-profiles" className="text-primary hover:underline">
                {t("workspaceDetail.scans.setupAuth")}
              </Link>
            </div>
          </div>
        </Empty>
      ) : filtered.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("workspaceDetail.scans.noMatch")}</p>
      ) : (
        <Card className="overflow-hidden p-0">
          <Table>
            <colgroup>
              <col style={{ width: 34 }} /><col style={{ width: 112 }} /><col />
              <col style={{ width: 190 }} /><col style={{ width: 104 }} /><col style={{ width: 172 }} />
              <col style={{ width: 72 }} /><col style={{ width: 92 }} /><col style={{ width: 132 }} />
              <col style={{ width: 196 }} />
            </colgroup>
            <TableHeader>
              <TableRow>
                <TableHead className="w-9 pl-4" />
                <TableHead className="whitespace-nowrap text-[11px] font-semibold uppercase tracking-wider">{t("workspaceDetail.scans.table.status")}</TableHead>
                <TableHead className="whitespace-nowrap text-[11px] font-semibold uppercase tracking-wider">{t("workspaceDetail.scans.table.scan")}</TableHead>
                <TableHead className="whitespace-nowrap text-[11px] font-semibold uppercase tracking-wider">{t("workspaceDetail.scans.table.repo")}</TableHead>
                <TableHead className="whitespace-nowrap text-[11px] font-semibold uppercase tracking-wider">{t("workspaceDetail.scans.table.type")}</TableHead>
                <TableHead className="whitespace-nowrap text-[11px] font-semibold uppercase tracking-wider">{t("workspaceDetail.scans.table.progress")}</TableHead>
                <TableHead className="whitespace-nowrap text-right text-[11px] font-semibold uppercase tracking-wider">{t("workspaceDetail.scans.table.vulns")}</TableHead>
                <TableHead className="whitespace-nowrap text-right text-[11px] font-semibold uppercase tracking-wider">{t("workspaceDetail.scans.table.cost")}</TableHead>
                <TableHead className="whitespace-nowrap text-[11px] font-semibold uppercase tracking-wider">{t("workspaceDetail.scans.table.created")}</TableHead>
                <TableHead className="whitespace-nowrap pr-4 text-right text-[11px] font-semibold uppercase tracking-wider">{t("workspaceDetail.scans.table.actions")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((s) => (
                <ScanRow key={s.scan_id} ws={workspace!} scan={s} onChanged={reload} />
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </div>
  );
}

/** 表格主行 + 嵌套黑盒 run 子行（可展开）。组合任务默认展开（预览示意 + 常显语义保留）。 */
function ScanRow({ ws, scan, onChanged }: { ws: string; scan: ScanSummary; onChanged: () => void }) {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<"cancel" | "delete" | null>(null);
  const isCombined = scan.combined === true;
  const hasRuns = isCombined && (scan.bb_runs?.length ?? 0) > 0;
  const [open, setOpen] = useState(hasRuns);

  const isRunning = isRun(scan);
  const isTerminal = TERMINAL.has(scan.status);
  // 恢复仅未完成（非 running 非终态，如 interrupted）；running 在跑无需恢复，终态不可恢复。
  const canResume = !isRunning && !isTerminal;
  const scanPath = `/p/${ws}/scans/${scan.scan_id}`;
  // 任务名展示用 workflow_id（{ws}-{scan_id}[-resume-N]），路由/API 仍用 scan_id 定位目录。
  const label = scan.workflow_id ?? scan.scan_id;
  // 默认进 scan 详情的 tab：完成 -> report（看结果），其余 -> live（看实时）。
  // 与 router.tsx DefaultScanTab 一致；此处据 scan.status 直定，免走 DefaultScanTab 多一次 getScan + 空白闪烁。
  const defaultTab = scan.status === "completed" || scan.status === "done" ? "report" : "live";

  // 运行中行按需建 SSE 推实时阶段（粗粒度：段标签后缀）；终态/非运行中不建（url=""）。
  // 列表页粗粒度——精确步级/Agent 在扫描详情页顶部。scan_end → 刷新列表拿终态（漏洞数/状态）。
  const sseUrl = isRunning ? scanEventsUrl(ws, scan.scan_id) : "";
  const { events } = useEventSource(sseUrl);
  const currentPhase = useCurrentPhase(events);
  useEffect(() => {
    if (events.some((e) => e.type === "scan_end")) onChanged();
  }, [events, onChanged]);

  async function onResume() {
    setBusy(true);
    try {
      await resumeScan(ws, scan.scan_id);
      toast.success(t("workspaceDetail.scans.resumed"));
      nav(`${scanPath}/live`);
    } catch (e) {
      toast.error(t("workspaceDetail.scans.resumeFailed", { error: msg(e) }));
    } finally {
      setBusy(false);
    }
  }

  // 重跑 = 同 ws 新建扫描（spec §12.7），按原扫描类型跳对应 tab 并预填相关数据。
  // 调 getScan 拿原配置（白盒 source_repo / 黑盒 web_url+reuse+auth）-> location state 传 ScanNewPage 预填。
  async function onRerun() {
    setBusy(true);
    try {
      const detail = await getScan(ws, scan.scan_id);
      const state: Record<string, unknown> = { type: scan.scan_type, workspace: ws };
      if (scan.scan_type === "whitebox") {
        if (detail.source_repo) state.repo = detail.source_repo;
      } else if (scan.scan_type === "blackbox") {
        if (detail.web_url) state.url = detail.web_url;
        if (detail.reuse_whitebox_scan_id) state.reuseScanId = detail.reuse_whitebox_scan_id;
        if (detail.authentication) state.auth = detail.authentication;
        // auth-profile-vault（Task 14）：profile 模式预填（后端 _scan_detail 暂未返此字段，
        // 前端先就位——后端补返 auth_profile_id+auth_credential_ids 时自动生效）。
        if ((detail as { auth_profile_id?: string | null }).auth_profile_id) {
          state.authProfileId = (detail as { auth_profile_id?: string | null }).auth_profile_id ?? undefined;
          state.authCredentialIds = (detail as { auth_credential_ids?: string[] | null }).auth_credential_ids ?? undefined;
        }
        // HOST source is a rerun input, not the old resolved mapping snapshot.
        if (detail.host_profile_id) state.hostProfileId = detail.host_profile_id;
        else if (detail.host_url) state.hostUrl = detail.host_url;
      }
      nav(`/scan/new?workspace=${encodeURIComponent(ws)}`, { state });
    } catch {
      // getScan 失败降级：只带 workspace 跳转（对齐旧现状），toast 提示用户手填。
      toast.error(t("workspaceDetail.scans.rerunLoadFailed"));
      nav(`/scan/new?workspace=${encodeURIComponent(ws)}`);
    } finally {
      setBusy(false);
    }
  }

  async function doAction() {
    if (!pending) return;
    setBusy(true);
    try {
      if (pending === "cancel") {
        await cancelScan(ws, scan.scan_id);
        toast.success(t("workspaceDetail.scans.canceled", { scanId: label }));
      } else {
        await deleteScan(ws, scan.scan_id);
        toast.success(t("workspaceDetail.scans.deleted", { scanId: label }));
      }
      setPending(null);
      onChanged();
    } catch (e) {
      toast.error(t(
        pending === "cancel" ? "workspaceDetail.scans.cancelFailed" : "workspaceDetail.scans.deleteFailed",
        { error: msg(e) },
      ));
    } finally {
      setBusy(false);
    }
  }

  const v = scan.vuln_count ?? 0;
  const pct = Math.max(0, Math.min(100, Math.round(scan.progress_pct ?? 0)));
  const dur = fmtDur(scan.total_duration_ms);

  return (
    <>
      {/* 整行可点（v4）：与 ID/查看同目标（defaultTab）；行内交互元素 stopPropagation 防误触 */}
      <TableRow onClick={() => nav(`${scanPath}/${defaultTab}`)} className="cursor-pointer">
        {/* 展开柄：仅组合任务带 bb_runs 时显（纯白盒/黑盒无子行不占交互） */}
        <TableCell className="w-9 pl-4">
          {hasRuns && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
              aria-expanded={open}
              aria-label={t(open ? "workspaceDetail.scans.runs.toggleCollapse" : "workspaceDetail.scans.runs.toggleExpand")}
              className="flex size-5 items-center justify-center rounded text-muted-foreground transition-colors hover:text-foreground"
            >
              <ChevronRight className={`size-3.5 transition-transform ${open ? "rotate-90" : ""}`} />
            </button>
          )}
        </TableCell>
        <TableCell><StatusBadge status={scan.status} /></TableCell>
        <TableCell className="max-w-0 truncate font-mono">
          <Link
            to={`${scanPath}/${defaultTab}`}
            onClick={(e) => e.stopPropagation()}
            className="text-[13px] font-medium hover:text-primary"
          >
            {label}
          </Link>
        </TableCell>
        {/* 仓库格：repo@branch（分支快照，spec 2026-08-21 §4）——切分支后同一仓扫不同
            分支靠此区分；commit 前 8 位进 title hover；无快照（存量/黑盒）只显 repo 名 */}
        <TableCell
          className="max-w-0"
          title={scan.repo_commit ? scan.repo_commit.slice(0, 8) : scan.repo_url ?? undefined}
        >
          <span className="block truncate text-[13px] font-medium">
            {scan.repo ?? "—"}
            {scan.repo_branch && (
              <span className="font-mono text-[11px] text-muted-foreground">@{scan.repo_branch}</span>
            )}
          </span>
          {scan.repo_url && (
            <span className="block truncate font-mono text-[10.5px] text-muted-foreground/80">{compactUrl(scan.repo_url)}</span>
          )}
        </TableCell>
        {/* 类型格：组合任务只显「组合」徽标——scan_type 底层仍为 whitebox（spec 2026-08-12 §6.2），
            whitebox+组合双徽标冗余，2026-08-17 起组合单显 */}
        <TableCell>
          {scan.combined === true ? (
            <Badge variant="outline" className="border-primary/35 font-mono text-primary">
              {t("workspaceDetail.scans.typeCombined")}
            </Badge>
          ) : (
            <Badge variant="outline" className="font-mono">{scan.scan_type}</Badge>
          )}
        </TableCell>
        <TableCell>
          {isRunning ? (
            <div className="flex min-w-[110px] flex-col gap-1">
              <div className="flex items-baseline gap-1.5">
                <span className="font-mono text-[13px] font-semibold leading-none">{pct}%</span>
                <span className="whitespace-nowrap text-[11px] text-muted-foreground">
                  {scanSegmentLabel(scan, currentPhase, t)}
                </span>
              </div>
              <span
                role="progressbar"
                aria-valuenow={pct}
                aria-valuemin={0}
                aria-valuemax={100}
                className="block h-1 w-full overflow-hidden rounded-full bg-muted"
              >
                <span className="block h-full rounded-full bg-primary transition-all" style={{ width: `${pct}%` }} />
              </span>
            </div>
          ) : scan.status === "completed" || scan.status === "done" ? (
            <span className="whitespace-nowrap font-mono text-xs text-muted-foreground">
              100%{dur && ` · ${t("workspaceDetail.scans.duration", { dur })}`}
            </span>
          ) : isTerminal ? (
            <span className="text-xs text-muted-foreground">—</span>
          ) : scan.progress_pct != null ? (
            <span className="whitespace-nowrap font-mono text-xs text-muted-foreground">
              {t("workspaceDetail.scans.stoppedAt", { pct })}
            </span>
          ) : (
            <span className="text-xs text-muted-foreground">—</span>
          )}
        </TableCell>
        <TableCell className="text-right">
          <span
            data-testid={`row-vulns-${scan.scan_id}`}
            className={`font-mono text-base font-semibold leading-none ${v > 0 ? "text-red" : "text-muted-foreground/70"}`}
          >
            {v}
          </span>
        </TableCell>
        <TableCell className="whitespace-nowrap text-right font-mono text-[13px]">
          {fmtCost(scan.total_cost_usd ?? null, scan.cost_currency ?? null)}
        </TableCell>
        <TableCell className="whitespace-nowrap font-mono text-xs text-muted-foreground" title={fmtTimeFull(scan.created_at)}>
          {fmtTime(scan.created_at)}
        </TableCell>
        {/* 操作组（预览 2026-08-15）：running=取消/查看/删除；未完成=恢复/查看/删除；终态=查看/重跑/删除。
            容器 stopPropagation——按钮动作不触发整行导航。 */}
        <TableCell className="whitespace-nowrap pr-4 text-right">
          <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
            {canResume && (
              <Button size="sm" variant="ghost" onClick={onResume} disabled={busy}>
                <Play className="size-3.5" /> {t("workspaceDetail.scans.resume")}
              </Button>
            )}
            {isRunning && (
              <Button size="sm" variant="ghost" onClick={() => setPending("cancel")} disabled={busy}>
                <Ban className="size-3.5" /> {t("common.cancel")}
              </Button>
            )}
            <Button size="sm" variant="ghost" asChild>
              <Link
                to={`${scanPath}/${defaultTab}`}
                onClick={(e) => e.stopPropagation()}
              >
                <Eye className="size-3.5" /> {t("workspaceDetail.scans.view")}
              </Link>
            </Button>
            {isTerminal && (
              <Button size="sm" variant="ghost" onClick={onRerun} disabled={busy}>
                <RefreshCw className="size-3.5" /> {t("workspaceDetail.scans.rerun")}
              </Button>
            )}
            <Button
              size="sm"
              variant="ghost"
              className="text-destructive hover:bg-destructive/10"
              aria-label={t("common.delete")}
              title={t("common.delete")}
              onClick={() => setPending("delete")}
              disabled={busy}
            >
              <Trash2 className="size-3.5" />
            </Button>
          </div>
        </TableCell>
      </TableRow>

      {/* 版本化黑盒 run（spec 2026-08-14 §5.2）：嵌套展开子行（组合任务默认展开）。
          纯白盒（无 bb_runs）不渲染。 */}
      {open && hasRuns && (
        <TableRow>
          <TableCell colSpan={10} className="px-0 py-0">
            <NestedBlackboxRuns
              ws={ws}
              scanId={scan.scan_id}
              runs={scan.bb_runs!}
              latestRunId={scan.latest_bb_run ?? null}
              onChanged={onChanged}
            />
          </TableCell>
        </TableRow>
      )}

      {/* 取消/删除确认 Dialog */}
      <Dialog open={!!pending} onOpenChange={(o) => !o && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {pending === "cancel"
                ? t("workspaceDetail.scans.cancelConfirmTitle")
                : t("workspaceDetail.scans.deleteConfirmTitle")}
            </DialogTitle>
            <DialogDescription>
              {pending === "cancel"
                ? t("workspaceDetail.scans.cancelConfirmDesc", { scanId: label })
                : t("workspaceDetail.scans.deleteConfirmDesc", { scanId: label })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPending(null)}>{t("common.cancel")}</Button>
            <Button variant="destructive" disabled={busy} onClick={doAction}>{t("common.confirm")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

/** 版本化黑盒 run 嵌套列表（spec 2026-08-14 §5.2）：展开子行内显每个 run 的 id + 状态 +
 *  最新 tag + 跳转到该 run 详情/报告的链接（?run= 选中）+ 终态 run 可删。 */
function NestedBlackboxRuns({ ws, scanId, runs, latestRunId, onChanged }: {
  ws: string; scanId: string; runs: BlackboxRunSummary[]; latestRunId: string | null; onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function doDelete() {
    if (!pendingDelete) return;
    setBusy(true);
    try {
      await deleteBlackboxRun(ws, scanId, pendingDelete);
      toast.success(t("workspaceDetail.scans.runs.deleted", { runId: pendingDelete }));
      setPendingDelete(null);
      onChanged();
    } catch (e) {
      toast.error(t("workspaceDetail.scans.runs.deleteFailed", { error: msg(e) }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="mx-4 mb-3 ml-12 flex flex-col gap-1.5 border-l-2 border-border py-1 pl-4" data-testid="nested-runs">
        {runs.map((r) => (
          <div key={r.run_id} className="flex items-center gap-2.5 text-[12.5px]">
            <span className="font-mono text-muted-foreground">{r.run_id}</span>
            <StatusBadge status={(r.status ?? r.bb_phase ?? "unknown") as never} />
            {r.run_id === latestRunId && (
              <span className="rounded-full border border-primary/35 px-1.5 py-px text-[10px] text-primary">
                {t("workspaceDetail.scans.runs.latest")}
              </span>
            )}
            <Link
              to={`/p/${ws}/scans/${scanId}?run=${r.run_id}`}
              className="text-xs text-primary hover:underline"
            >
              {t("workspaceDetail.scans.runs.view")}
            </Link>
            <Button
              size="icon"
              variant="ghost"
              className="size-6 text-muted-foreground hover:text-destructive"
              aria-label={t("workspaceDetail.scans.runs.delete")}
              disabled={!isRunTerminal(r.status)}
              title={isRunTerminal(r.status) ? undefined : t("workspaceDetail.scans.runs.deleteRunningHint")}
              onClick={(e) => { e.preventDefault(); e.stopPropagation(); setPendingDelete(r.run_id); }}
            >
              <Trash2 className="size-3.5" />
            </Button>
          </div>
        ))}
      </div>
      {/* 删除 run 确认 Dialog */}
      <Dialog open={!!pendingDelete} onOpenChange={(o) => !o && setPendingDelete(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("workspaceDetail.scans.runs.deleteConfirmTitle")}</DialogTitle>
            <DialogDescription>
              {t("workspaceDetail.scans.runs.deleteConfirmDesc", { runId: pendingDelete })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPendingDelete(null)} disabled={busy}>
              {t("common.cancel")}
            </Button>
            <Button variant="destructive" onClick={doDelete} disabled={busy}>
              {busy && <RefreshCw className="mr-1 size-3.5 animate-spin" />}
              {t("common.confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function msg(e: unknown): string {
  if (e instanceof ApiError) return String(e.status);
  return e instanceof Error ? e.message : String(e);
}
