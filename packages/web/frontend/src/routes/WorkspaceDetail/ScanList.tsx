import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams, useNavigate, Link } from "react-router-dom";
import { toast } from "sonner";
import { Ban, Play, RefreshCw, Trash2, Eye } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/StatusBadge";
import { ErrorState } from "@/components/ErrorState";
import { Empty } from "@/components/Empty";
import { ScanFilters, DEFAULT_SCAN_FILTERS, useScanFilters } from "@/components/ScanFilters";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
  listScans, cancelScan, deleteScan, deleteBlackboxRun, resumeScan, getScan, scanEventsUrl, ApiError,
} from "@/api/client";
import { useEventSource } from "@/api/useEventSource";
import type { BlackboxRunSummary, ScanSummary, SessionData } from "@/api/types";
import { fmtCost } from "@/utils/currency";
import { isRunTerminal } from "./runStatus";
import { ScanProgressBadge } from "./ScanProgressBadge";

// 终态集（spec §5.1 resume 仅非终态放行，终态 422）。interrupted 等属未完成可恢复。
const TERMINAL = new Set(["completed", "done", "failed", "killed", "crashed"]);

// 运行中卡轮询间隔（静默刷新 listScans → progress_pct 实时推进；终态卡不轮询）。
const POLL_INTERVAL_MS = 10_000;

function fmtTime(unix?: number | null): string {
  if (!unix) return "-";
  return new Date(unix * 1000).toLocaleString();
}

/** 从 SSE events 推当前阶段（最后一条 PhaseEvent(start).phase）；无则 null。
 *  列表卡粗粒度用：纯白盒/纯黑盒段标签后缀（如「白盒 · recon」）。 */
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
  const [scans, setScans] = useState<ScanSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [filters, setFilters] = useState(DEFAULT_SCAN_FILTERS);

  const load = () => {
    if (!workspace) return;
    setLoading(true);
    setErr(null);
    listScans(workspace)
      .then(setScans)
      .catch((e: unknown) => { setErr(String(e)); setScans([]); })
      .finally(() => setLoading(false));
  };

  useEffect(load, [workspace]);

  // 运行中卡存在时静默轮询刷新（progress_pct 实时推进 → x% 动）；终态卡不轮询。
  // 静默（不 setLoading）→ 不闪 Skeleton → ScanCard 不卸载 → 运行中卡 SSE 连接保持、步级不重置。
  const hasRunning = scans.some((s) => s.is_running || s.status === "running");
  useEffect(() => {
    if (!hasRunning || !workspace) return;
    const id = setInterval(() => {
      listScans(workspace).then(setScans).catch(() => {});
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [hasRunning, workspace]);

  if (err) return <ErrorState message={t("workspaceDetail.scans.loadError", { error: err })} />;

  const { filtered } = useScanFilters(scans, filters);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold tracking-tight text-base">{t("workspaceDetail.scans.listTitle")}</h3>
        {workspace && (
          <Button variant="cta" asChild>
            <Link to={`/scan/new?workspace=${encodeURIComponent(workspace)}`}>
              {t("workspaceDetail.scans.newScan")}
            </Link>
          </Button>
        )}
      </div>

      <ScanFilters value={filters} onChange={setFilters} />

      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-20 w-full" />)}
        </div>
      ) : scans.length === 0 ? (
        <Empty title={t("workspaceDetail.scans.empty")} hint={t("workspaceDetail.scans.emptyHint")}>
          {workspace && (
            <Button asChild>
              <Link to={`/scan/new?workspace=${encodeURIComponent(workspace)}`}>
                {t("workspaceDetail.scans.newScan")}
              </Link>
            </Button>
          )}
        </Empty>
      ) : filtered.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("workspaceDetail.scans.noMatch")}</p>
      ) : (
        filtered.map((s) => (
          <ScanCard key={s.scan_id} ws={workspace!} scan={s} onChanged={load} />
        ))
      )}
    </div>
  );
}

/** 版本化黑盒 run 嵌套列表（spec 2026-08-14 §5.2）：组合任务卡内显每个 run 的 id + 状态 +
 *  跳转到该 run 详情/报告的链接（?run= 选中）。非组合卡不渲染（零回归）。 */
function NestedBlackboxRuns({ ws, scanId, runs, onChanged }: {
  ws: string; scanId: string; runs: BlackboxRunSummary[]; onChanged: () => void;
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
      <ul className="ml-4 mt-2 space-y-1 border-l pl-3" data-testid="nested-runs">
        {runs.map((r) => (
          <li key={r.run_id} className="flex items-center gap-2 text-sm">
            <span className="font-mono text-muted-foreground">{r.run_id}</span>
            <StatusBadge status={(r.status ?? r.bb_phase ?? "unknown") as never} />
            <Link
              to={`/p/${ws}/scans/${scanId}?run=${r.run_id}`}
              className="text-primary text-xs hover:underline"
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
          </li>
        ))}
      </ul>
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

function ScanCard({ ws, scan, onChanged }: { ws: string; scan: ScanSummary; onChanged: () => void }) {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<"cancel" | "delete" | null>(null);
  const isCombined = scan.combined === true;

  const isRunning = scan.is_running || scan.status === "running";
  const isTerminal = TERMINAL.has(scan.status);
  // 恢复仅未完成（非 running 非终态，如 interrupted）；running 在跑无需恢复，终态不可恢复。
  const canResume = !isRunning && !isTerminal;
  const scanPath = `/p/${ws}/scans/${scan.scan_id}`;
  // 任务名展示用 workflow_id（{ws}-{scan_id}[-resume-N]），路由/API 仍用 scan_id 定位目录。
  const label = scan.workflow_id ?? scan.scan_id;
  // 默认进 scan 详情的 tab：完成 -> report（看结果），其余 -> live（看实时）。
  // 与 router.tsx DefaultScanTab 一致；此处据 scan.status 直定，免走 DefaultScanTab 多一次 getScan + 空白闪烁。
  const defaultTab = scan.status === "completed" || scan.status === "done" ? "report" : "live";

  // 运行中卡按需建 SSE 推实时阶段（粗粒度：currentPhase 段标签后缀）；终态/非运行中不建（url=""）。
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
        if ((detail as SessionData).auth_profile_id) {
          state.authProfileId = (detail as SessionData).auth_profile_id ?? undefined;
          state.authCredentialIds = (detail as SessionData).auth_credential_ids ?? undefined;
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

  return (
    <Card className="px-4 py-2.5">
      {/* 单行：scan 标识 + 状态/类型 + 内联 hero 指标（漏洞/花费 大号 mono）+ 进度徽标 + [右锚固定操作组]。
         漏洞数 >0 染红——安全工具里「发现」是头条；=0 中性不刺眼。
         进度徽标（运行中卡）：x% + 进度条 + 段标签（白盒/黑盒/组合段），所有类型统一粗粒度；
         终态卡徽标组件返回 null 不占位。右侧操作组 shrink-0 + flex-nowrap + ml-auto 右锚永不换行 ——
         View/Rerun/Delete 三常驻按钮永远是固定列；Resume/Cancel（互斥：running↔非终态非running）
         出现在常驻组左侧，缺席不挪位。 */}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5">
          <Link to={`${scanPath}/${defaultTab}`} className="font-mono text-sm font-medium hover:text-primary">
            {label}
          </Link>
          <StatusBadge status={scan.status} />
          <Badge variant="outline" className="font-mono">{scan.scan_type}</Badge>
          <span className="inline-flex items-baseline gap-1">
            <span className="text-xs text-muted-foreground">{t("workspaceDetail.scans.card.vulns")}</span>
            <span
              className={`font-mono text-lg font-semibold leading-none ${
                (scan.vuln_count ?? 0) > 0 ? "text-red" : "text-foreground"
              }`}
            >
              {String(scan.vuln_count ?? 0)}
            </span>
          </span>
          <span className="inline-flex items-baseline gap-1">
            <span className="text-xs text-muted-foreground">{t("workspaceDetail.scans.card.cost")}</span>
            <span className="font-mono text-lg leading-none">
              {fmtCost(scan.total_cost_usd ?? null, scan.cost_currency ?? null)}
            </span>
          </span>
          <span className="inline-flex items-baseline gap-1">
            <span className="text-xs text-muted-foreground">{t("workspaceDetail.scans.card.created")}</span>
            <span className="font-mono text-sm text-muted-foreground">{fmtTime(scan.created_at)}</span>
          </span>
          <ScanProgressBadge scan={scan} currentPhase={currentPhase} />
        </div>
        {/* 右锚操作组：永不换行 + shrink-0。View/Rerun/Delete 常驻且顺序固定 → 固定列；
            Resume/Cancel 互斥仅其一，出现在常驻组左侧，缺席时常驻组纹丝不动。 */}
        <div className="ml-auto flex shrink-0 flex-nowrap items-center gap-1">
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
          {(canResume || isRunning) && (
            <span className="mx-0.5 h-5 w-px self-center bg-border" aria-hidden />
          )}
          <Button size="sm" variant="ghost" asChild>
            <Link to={`${scanPath}/${defaultTab}`}><Eye className="size-3.5" /> {t("workspaceDetail.scans.view")}</Link>
          </Button>
          <Button size="sm" variant="ghost" onClick={onRerun} disabled={busy}>
            <RefreshCw className="size-3.5" /> {t("workspaceDetail.scans.rerun")}
          </Button>
          <Button size="sm" variant="ghost" className="text-destructive hover:bg-destructive/10" onClick={() => setPending("delete")} disabled={busy}>
            <Trash2 className="size-3.5" /> {t("common.delete")}
          </Button>
        </div>
      </div>

      {/* 版本化黑盒 run（spec 2026-08-14）：组合任务卡内嵌 run 列表。常显（不依赖 expanded），
          让收起态也能一眼看到 run 数 + 跳转最新 run 报告。纯白盒（无 bb_runs）不渲染。 */}
      {isCombined && scan.bb_runs && scan.bb_runs.length > 0 && (
        <NestedBlackboxRuns ws={ws} scanId={scan.scan_id} runs={scan.bb_runs} onChanged={onChanged} />
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
    </Card>
  );
}

function msg(e: unknown): string {
  if (e instanceof ApiError) return String(e.status);
  return e instanceof Error ? e.message : String(e);
}


