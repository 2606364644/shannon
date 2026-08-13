import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams, useNavigate, Link } from "react-router-dom";
import { toast } from "sonner";
import { Ban, Play, RefreshCw, Trash2, Eye, ChevronDown } from "lucide-react";
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
  listScans, cancelScan, deleteScan, resumeScan, getScan, scanEventsUrl, ApiError,
} from "@/api/client";
import { useEventSource } from "@/api/useEventSource";
import type { BlackboxRunSummary, ScanSummary, SessionData, NdjsonEvent } from "@/api/types";
import { fmtCost } from "@/utils/currency";
import { cn } from "@/lib/utils";

// 终态集（spec §5.1 resume 仅非终态放行，终态 422）。interrupted 等属未完成可恢复。
const TERMINAL = new Set(["completed", "done", "failed", "killed", "crashed"]);

function fmtTime(unix?: number | null): string {
  if (!unix) return "-";
  return new Date(unix * 1000).toLocaleString();
}

// === 组合扫描进度卡片（spec 2026-08-12-combined-wb-bb-scan §9）===
// 收起态：progress_pct% + 进度条 + 阶段名（bb_phase 映射）——所有列表卡片轻量呈现。
// 展开态：按需读 events.ndjson 推步级（PhaseEvent declared steps vs StepEvent complete）。
// 非组合（combined!=true）卡片走原单段渲染，零回归。

// bb_phase → i18n key（spec §6.2 状态机：precheck/pending/running/completed/failed/skipped）。
const BB_PHASE_LABEL_KEY: Record<string, string> = {
  precheck: "workspaceDetail.scans.combined.phasePrecheck",
  pending: "workspaceDetail.scans.combined.phasePending",
  running: "workspaceDetail.scans.combined.phaseRunning",
  completed: "workspaceDetail.scans.combined.phaseCompleted",
  failed: "workspaceDetail.scans.combined.phaseFailed",
  skipped: "workspaceDetail.scans.combined.phaseSkipped",
};

/** 收起态：progress_pct + 进度条 + 阶段名。仅 combined 卡片渲染。 */
function CombinedProgressBadge({ scan }: { scan: ScanSummary }) {
  const { t } = useTranslation();
  const pct = Math.max(0, Math.min(100, Math.round(scan.progress_pct ?? 0)));
  const phaseKey = BB_PHASE_LABEL_KEY[scan.bb_phase ?? ""] ??
    "workspaceDetail.scans.combined.phaseUnknown";
  return (
    <span className="inline-flex items-center gap-2" data-testid="combined-progress">
      <span className="font-mono text-sm font-semibold leading-none">{pct}%</span>
      <span
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        className="inline-block h-1.5 w-24 overflow-hidden rounded-full bg-muted"
      >
        <span
          className="block h-full rounded-full bg-primary transition-all"
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className="text-xs text-muted-foreground">{t(phaseKey)}</span>
    </span>
  );
}

/** 单 phase 的步级进度（PhaseEvent(start) 声明 vs StepEvent(complete) 计数）。 */
interface PhaseStepProgress {
  total: number;
  completed: number;
}

/** 从 events 推 phase→步级进度映射（保留插入顺序）。 */
function derivePhaseSteps(events: NdjsonEvent[]): Array<[string, PhaseStepProgress]> {
  const map = new Map<string, PhaseStepProgress>();
  for (const ev of events) {
    if (ev.type === "PhaseEvent" && ev.event === "start") {
      // 同 phase 重入（白盒 phase 在黑盒段同名时按首次声明计），保留先入声明。
      if (!map.has(ev.phase)) {
        const steps = Array.isArray(ev.steps) ? ev.steps : [];
        map.set(ev.phase, { total: steps.length, completed: 0 });
      }
    } else if (ev.type === "StepEvent" && ev.event === "complete") {
      const p = map.get(ev.phase);
      if (p) p.completed += 1;
    }
  }
  return Array.from(map.entries());
}

/**
 * 展开态：按需拉该 scan 的 events 推步级。仅展开时挂载（条件渲染）→
 * useEventSource 仅在展开时建 SSE 连接，收起时卸载自动 close（on-demand，非 eager）。
 * 分段靠 bb_phase（spec §9.4 D3）：bb_phase=pending 时只显白盒段；running 之后白盒+黑盒段都显。
 */
function CombinedStepTimeline({
  ws, scanId, bbPhase,
}: { ws: string; scanId: string; bbPhase?: string }) {
  const { t } = useTranslation();
  const { events } = useEventSource(scanEventsUrl(ws, scanId));
  const phases = useMemo(() => derivePhaseSteps(events), [events]);

  if (phases.length === 0) {
    return (
      <div className="mt-2 border-t border-border/60 pt-2 text-xs text-muted-foreground">
        {t("workspaceDetail.scans.combined.noSteps")}
      </div>
    );
  }

  // 黑盒段是否已开始：bb_phase ∈ running/completed/failed（pending/precheck/skipped = 黑盒未跑或跳过）。
  const blackboxStarted = bbPhase === "running" || bbPhase === "completed" || bbPhase === "failed";

  return (
    <div className="mt-2 space-y-1.5 border-t border-border/60 pt-2">
      <div className="text-xs font-medium text-muted-foreground">
        {t("workspaceDetail.scans.combined.stepLevelTitle")}
      </div>
      {phases.map(([phase, p]) => (
        <div key={phase} className="flex items-center gap-2 text-xs">
          <span className="font-mono text-muted-foreground">{phase}</span>
          <span className="font-mono font-medium">{p.completed}/{p.total}</span>
          <span className="h-1 flex-1 overflow-hidden rounded-full bg-muted">
            <span
              className="block h-full rounded-full bg-primary/80"
              style={{ width: `${p.total > 0 ? Math.round((p.completed / p.total) * 100) : 0}%` }}
            />
          </span>
        </div>
      ))}
      {!blackboxStarted && (
        <div className="text-xs italic text-muted-foreground">
          {t("workspaceDetail.scans.combined.segmentBlackboxPending")}
        </div>
      )}
    </div>
  );
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
function NestedBlackboxRuns({ ws, scanId, runs }: {
  ws: string; scanId: string; runs: BlackboxRunSummary[];
}) {
  const { t } = useTranslation();
  return (
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
        </li>
      ))}
    </ul>
  );
}

function ScanCard({ ws, scan, onChanged }: { ws: string; scan: ScanSummary; onChanged: () => void }) {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<"cancel" | "delete" | null>(null);
  // 组合扫描卡片展开/收起（按需读 events 推步级，spec §9.3）。
  const [expanded, setExpanded] = useState(false);
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
      {/* 单行：scan 标识 + 状态/类型 + 内联 hero 指标（漏洞/花费 大号 mono）+ 操作。
         漏洞数 >0 染红——安全工具里「发现」是头条；=0 中性不刺眼。*/}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
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
          {isCombined && <CombinedProgressBadge scan={scan} />}
        </div>
        <div className="flex flex-wrap items-center gap-1">
          {isCombined && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
              aria-label={expanded
                ? t("workspaceDetail.scans.combined.collapse")
                : t("workspaceDetail.scans.combined.expand")}
            >
              <ChevronDown className={cn("size-3.5 transition-transform", expanded && "rotate-180")} />
              {expanded
                ? t("workspaceDetail.scans.combined.collapse")
                : t("workspaceDetail.scans.combined.expand")}
            </Button>
          )}
          <Button size="sm" variant="ghost" asChild>
            <Link to={`${scanPath}/${defaultTab}`}><Eye className="size-3.5" /> {t("workspaceDetail.scans.view")}</Link>
          </Button>
          {canResume && (
            <Button size="sm" variant="ghost" onClick={onResume} disabled={busy}>
              <Play className="size-3.5" /> {t("workspaceDetail.scans.resume")}
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={onRerun} disabled={busy}>
            <RefreshCw className="size-3.5" /> {t("workspaceDetail.scans.rerun")}
          </Button>
          {isRunning && (
            <Button size="sm" variant="ghost" onClick={() => setPending("cancel")} disabled={busy}>
              <Ban className="size-3.5" /> {t("common.cancel")}
            </Button>
          )}
          <Button size="sm" variant="ghost" className="text-destructive hover:bg-destructive/10" onClick={() => setPending("delete")} disabled={busy}>
            <Trash2 className="size-3.5" /> {t("common.delete")}
          </Button>
        </div>
      </div>

      {/* 组合扫描展开态：按需步级时间线（spec §9.3）。仅在 expanded 时挂载 →
          useEventSource 仅此刻建 SSE，收起时卸载自动 close（on-demand，非 eager）。 */}
      {isCombined && expanded && (
        <CombinedStepTimeline ws={ws} scanId={scan.scan_id} bbPhase={scan.bb_phase} />
      )}

      {/* 版本化黑盒 run（spec 2026-08-14）：组合任务卡内嵌 run 列表。常显（不依赖 expanded），
          让收起态也能一眼看到 run 数 + 跳转最新 run 报告。纯白盒（无 bb_runs）不渲染。 */}
      {isCombined && scan.bb_runs && scan.bb_runs.length > 0 && (
        <NestedBlackboxRuns ws={ws} scanId={scan.scan_id} runs={scan.bb_runs} />
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


