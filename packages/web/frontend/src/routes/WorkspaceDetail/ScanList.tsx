import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams, useNavigate, Link } from "react-router-dom";
import { toast } from "sonner";
import { Play, RefreshCw, Trash2, Eye } from "lucide-react";
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
  listScans, cancelScan, deleteScan, resumeScan, ApiError,
} from "@/api/client";
import type { ScanSummary } from "@/api/types";
import { fmtCost } from "@/utils/currency";

// 终态集（spec §5.1 resume 仅非终态放行，终态 422）。interrupted 等属未完成可恢复。
const TERMINAL = new Set(["completed", "done", "failed", "killed", "crashed"]);

function fmtTime(unix?: number | null): string {
  if (!unix) return "-";
  return new Date(unix * 1000).toLocaleString();
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

function ScanCard({ ws, scan, onChanged }: { ws: string; scan: ScanSummary; onChanged: () => void }) {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<"cancel" | "delete" | null>(null);

  const isRunning = scan.is_running || scan.status === "running";
  const isTerminal = TERMINAL.has(scan.status);
  // 恢复仅未完成（非 running 非终态，如 interrupted）；running 在跑无需恢复，终态不可恢复。
  const canResume = !isRunning && !isTerminal;
  const scanPath = `/p/${ws}/scans/${scan.scan_id}`;

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

  // 重跑 = 同 ws 新建扫描（spec §12.7：workspace 无再次扫描入口，只有新建扫描）。
  // 跳 ScanNewPage 预填 ws，source 用户重选（贴合「新建扫描」心智）。
  function onRerun() {
    nav(`/scan/new?workspace=${encodeURIComponent(ws)}`);
  }

  async function doAction() {
    if (!pending) return;
    setBusy(true);
    try {
      if (pending === "cancel") {
        await cancelScan(ws, scan.scan_id);
        toast.success(t("workspaceDetail.scans.canceled", { scanId: scan.scan_id }));
      } else {
        await deleteScan(ws, scan.scan_id);
        toast.success(t("workspaceDetail.scans.deleted", { scanId: scan.scan_id }));
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
          <Link to={`${scanPath}/live`} className="font-mono text-sm font-medium hover:text-primary">
            {scan.scan_id}
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
        </div>
        <div className="flex flex-wrap items-center gap-1">
          <Button size="sm" variant="ghost" asChild>
            <Link to={`${scanPath}/live`}><Eye className="size-3.5" /> {t("workspaceDetail.scans.view")}</Link>
          </Button>
          {canResume && (
            <Button size="sm" variant="ghost" onClick={onResume} disabled={busy}>
              <Play className="size-3.5" /> {t("workspaceDetail.scans.resume")}
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={onRerun}>
            <RefreshCw className="size-3.5" /> {t("workspaceDetail.scans.rerun")}
          </Button>
          {isRunning && (
            <Button size="sm" variant="ghost" onClick={() => setPending("cancel")} disabled={busy}>
              {t("common.cancel")}
            </Button>
          )}
          <Button size="sm" variant="ghost" className="text-destructive hover:bg-destructive/10" onClick={() => setPending("delete")} disabled={busy}>
            <Trash2 className="size-3.5" /> {t("common.delete")}
          </Button>
        </div>
      </div>

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
                ? t("workspaceDetail.scans.cancelConfirmDesc", { scanId: scan.scan_id })
                : t("workspaceDetail.scans.deleteConfirmDesc", { scanId: scan.scan_id })}
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


