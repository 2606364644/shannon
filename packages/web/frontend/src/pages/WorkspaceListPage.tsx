import { Fragment, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { fmtCost } from "@/utils/currency";
import { Link } from "react-router-dom";
import {
  createColumnHelper, flexRender, getCoreRowModel,
  getExpandedRowModel, getFilteredRowModel, getSortedRowModel,
  SortingState, useReactTable,
} from "@tanstack/react-table";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Empty } from "@/components/Empty";
import { StatusBadge } from "@/components/StatusBadge";
import { useWorkspaces } from "@/api/useWorkspaces";
import { cancelScan, deleteWorkspace } from "@/api/client";
import type { Workspace } from "@/api/types";

const helper = createColumnHelper<Workspace>();

const STATUS_COLOR: Record<string, string> = {
  running: "bg-cyan",
  completed: "bg-green",
  done: "bg-green",
  failed: "bg-red",
  killed: "bg-red",
  crashed: "bg-yellow",
};
const statusColor = (s: string) => STATUS_COLOR[s] ?? "bg-yellow";

function fmtTime(unix?: number): string {
  if (!unix) return "—";
  return new Date(unix * 1000).toLocaleString();
}

export function WorkspaceListPage() {
  const { t } = useTranslation();
  const { data, loading, lastUpdated, error, refresh } = useWorkspaces();
  const [globalFilter, setGlobalFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [sorting, setSorting] = useState<SortingState>([{ id: "created_at", desc: true }]);

  // 待操作的 ws（取消/删除 Dialog）
  const [pendingAction, setPendingAction] = useState<{ ws: string; kind: "cancel" | "delete" } | null>(null);
  const [busy, setBusy] = useState(false);

  const filtered = useMemo(() => {
    let rows = data;
    if (statusFilter !== "all") rows = rows.filter((w) => w.status === statusFilter);
    if (typeFilter !== "all") rows = rows.filter((w) => w.scan_type === typeFilter);
    if (globalFilter.trim()) {
      const q = globalFilter.toLowerCase();
      rows = rows.filter((w) => w.name.toLowerCase().includes(q));
    }
    return rows;
  }, [data, statusFilter, typeFilter, globalFilter]);

  const columns = useMemo(() => [
    helper.display({
      id: "expand",
      header: () => "",
      cell: ({ row }) =>
        row.original.is_correlation ? (
          <button aria-label={t("workspaces.expandAria")} onClick={row.getToggleExpandedHandler()} className="text-muted-foreground">
            {row.getIsExpanded() ? "▼" : "▶"}
          </button>
        ) : null,
    }),
    helper.accessor("name", {
      header: () => t("workspaces.table.name"), cell: (info) => (
        <span className="flex min-w-0 items-center gap-2">
          <span className={`inline-block w-0.5 self-stretch rounded ${statusColor(info.row.original.status)}`} />
          <Tooltip>
            <TooltipTrigger asChild>
              {/* 长名截断（hostname_YYYYMMDD-HHMMSS 可达 30+ 字符），hover tooltip 看全名，防撑宽列表 */}
              <Link to={`/p/${info.getValue()}`} className="block max-w-[28ch] truncate font-mono hover:text-primary">{info.getValue()}</Link>
            </TooltipTrigger>
            <TooltipContent>{info.getValue()}</TooltipContent>
          </Tooltip>
          {info.row.original.is_correlation ? " 🔗" : ""}
        </span>
      ),
    }),
    helper.accessor("status", {
      header: () => t("workspaces.table.status"), cell: (info) => (
        <StatusBadge status={info.getValue()} correlation={!!info.row.original.is_correlation} />
      ),
    }),
    helper.accessor("scan_type", { header: () => t("workspaces.table.type"), cell: (info) => t(`workspaces.filter.${info.getValue()}`) }),
    helper.accessor("vuln_count", { header: () => t("workspaces.table.vulns"), cell: (info) => info.getValue() ?? "—" }),
    helper.accessor("total_cost_usd", {
      header: () => t("workspaces.table.cost"), cell: (info) => {
        const v = info.getValue();
        return v != null ? fmtCost(v, info.row.original.cost_currency) : "—";
      },
    }),
    helper.accessor("created_at", { header: () => t("workspaces.table.time"), cell: (info) => fmtTime(info.getValue()) }),
    helper.display({
      id: "actions", header: () => <div className="text-center">{t("workspaces.table.actions")}</div>, cell: (info) => {
        const w = info.row.original;
        // Delete 始终可见;running 额外显 Cancel(spec §4.7,去掉原 running XOR)。
        return (
          <div className="flex items-center justify-center gap-1">
            {w.status === "running" && (
              <Button size="sm" variant="ghost" onClick={() => setPendingAction({ ws: w.name, kind: "cancel" })}>{t("common.cancel")}</Button>
            )}
            <Button size="sm" variant="ghost" className="text-red" onClick={() => setPendingAction({ ws: w.name, kind: "delete" })}>{t("common.delete")}</Button>
          </div>
        );
      },
    }),
  ], [t]);

  const table = useReactTable({
    data: filtered, columns,
    state: { sorting, globalFilter },
    onSortingChange: setSorting,
    getRowCanExpand: (row) => !!row.original.is_correlation,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
  });

  async function doAction() {
    if (!pendingAction) return;
    setBusy(true);
    try {
      if (pendingAction.kind === "cancel") {
        const res = await cancelScan(pendingAction.ws);
        // 协作式取消语义提示(宿主 scan:已发信号 ≤30s 退;已死:直接标记)。
        if (res?.via === "signal") toast.info(t("workspaces.cancelViaSignal"));
        else if (res?.was_dead) toast.info(t("workspaces.cancelWasDead"));
      } else {
        await deleteWorkspace(pendingAction.ws);
      }
      await refresh();
      setPendingAction(null);
    } catch (e) {
      // API 失败:toast 错误 + 复位 busy(finally)+ 弹窗保留(用户可重试或取消,不卡死)。
      toast.error(t("workspaces.actionFailed", { error: e instanceof Error ? e.message : String(e) }));
    } finally {
      setBusy(false);
    }
  }

  const lastUpdatedStr = lastUpdated ? lastUpdated.toLocaleTimeString() : "—";

  return (
    <TooltipProvider>
    <div className="space-y-4">
      {/* 工具栏 */}
      <div className="flex flex-wrap items-center gap-3">
        <Input
          placeholder={t("workspaces.searchPlaceholder")}
          value={globalFilter}
          onChange={(e) => setGlobalFilter(e.target.value)}
          className="max-w-xs"
        />
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger aria-label={t("workspaces.statusFilterAria")} className="w-32"><SelectValue placeholder={t("workspaces.table.status")} /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("workspaces.filter.allStatus")}</SelectItem>
            <SelectItem value="running">{t("workspaces.status.running")}</SelectItem>
            <SelectItem value="completed">{t("workspaces.status.completed")}</SelectItem>
            <SelectItem value="failed">{t("workspaces.status.failed")}</SelectItem>
            <SelectItem value="killed">{t("workspaces.status.killed")}</SelectItem>
            <SelectItem value="crashed">{t("workspaces.status.crashed")}</SelectItem>
            <SelectItem value="interrupted">{t("workspaces.status.interrupted")}</SelectItem>
          </SelectContent>
        </Select>
        <Select value={typeFilter} onValueChange={setTypeFilter}>
          <SelectTrigger aria-label={t("workspaces.typeFilterAria")} className="w-32"><SelectValue placeholder={t("workspaces.table.type")} /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("workspaces.filter.allType")}</SelectItem>
            <SelectItem value="whitebox">{t("workspaces.filter.whitebox")}</SelectItem>
            <SelectItem value="blackbox">{t("workspaces.filter.blackbox")}</SelectItem>
            <SelectItem value="correlation">{t("workspaces.filter.correlation")}</SelectItem>
          </SelectContent>
        </Select>
        <Link to="/scan/new">
          <Button variant="outline" size="sm">{t("workspaces.newScan")}</Button>
        </Link>
        <div className="ml-auto flex items-center gap-3">
          <span className="text-xs text-muted-foreground">{t("workspaces.lastRefresh", { time: lastUpdatedStr })}</span>
          <Button variant="ghost" size="icon" aria-label={t("workspaces.refreshAria")} onClick={() => refresh()}>↻</Button>
        </div>
      </div>

      {error && <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-sm text-red">{error}</div>}

      {/* 表格 / 空态 / loading */}
      {loading && data.length === 0 ? (
        <div className="space-y-2">
          {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-8 w-full" />)}
        </div>
      ) : data.length === 0 ? (
        <Empty title={t("workspaces.empty.title")} hint={t("workspaces.empty.hint")}>
          <Link to="/scan/new"><Button>{t("workspaces.newScan")}</Button></Link>
        </Empty>
      ) : (
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((hg) => (
              <TableRow key={hg.id} className="hover:bg-transparent">
                {hg.headers.map((h) => (
                  <TableHead
                    key={h.id}
                    onClick={h.column.getCanSort() ? h.column.getToggleSortingHandler() : undefined}
                    className={h.column.getCanSort() ? "cursor-pointer" : undefined}
                  >
                    {flexRender(h.column.columnDef.header, h.getContext())}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows.map((row) => (
              <Fragment key={row.id}>
                <TableRow>
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</TableCell>
                  ))}
                </TableRow>
                {row.getIsExpanded() && row.original.is_correlation && (
                  <TableRow key={`${row.id}-expanded`}>
                    <TableCell colSpan={row.getVisibleCells().length} className="bg-muted/30">
                      <div className="flex flex-col gap-1 pl-6 text-sm">
                        {(row.original.links?.child_workspaces ?? []).length === 0 ? (
                          <span className="text-muted-foreground">{t("workspaces.noChild")}</span>
                        ) : (
                          (row.original.links?.child_workspaces ?? []).map((c) => (
                            <Link key={c} to={`/p/${c}`} className="font-mono hover:text-primary">└─ {c}</Link>
                          ))
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            ))}
          </TableBody>
        </Table>
      )}

      {/* 取消/删除确认 Dialog */}
      <Dialog open={!!pendingAction} onOpenChange={(o) => !o && setPendingAction(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{pendingAction?.kind === "cancel" ? t("workspaces.deleteDialog.cancelTitle") : t("workspaces.deleteDialog.deleteTitle")}</DialogTitle>
            <DialogDescription>
              {pendingAction?.kind === "cancel"
                ? t("workspaces.deleteDialog.cancelDesc", { ws: pendingAction?.ws })
                : t("workspaces.deleteDialog.deleteDesc", { ws: pendingAction?.ws })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPendingAction(null)}>{t("common.cancel")}</Button>
            <Button variant="destructive" disabled={busy} onClick={doAction}>{t("common.confirm")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
    </TooltipProvider>
  );
}
