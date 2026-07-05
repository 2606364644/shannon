import { Fragment, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  createColumnHelper, flexRender, getCoreRowModel,
  getExpandedRowModel, getFilteredRowModel, getSortedRowModel,
  SortingState, useReactTable,
} from "@tanstack/react-table";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
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
          <button aria-label="展开" onClick={row.getToggleExpandedHandler()} className="text-muted-foreground">
            {row.getIsExpanded() ? "▼" : "▶"}
          </button>
        ) : null,
    }),
    helper.accessor("name", {
      header: "workspace", cell: (info) => (
        <span className="flex items-center gap-2">
          <span className={`inline-block w-0.5 self-stretch rounded ${statusColor(info.row.original.status)}`} />
          <Link to={`/p/${info.getValue()}`} className="font-mono hover:text-primary">{info.getValue()}</Link>
          {info.row.original.is_correlation ? " 🔗" : ""}
        </span>
      ),
    }),
    helper.accessor("status", {
      header: "status", cell: (info) => (
        <StatusBadge status={info.getValue()} correlation={!!info.row.original.is_correlation} />
      ),
    }),
    helper.accessor("scan_type", { header: "type" }),
    helper.accessor("vuln_count", { header: "vulns", cell: (info) => info.getValue() ?? "—" }),
    helper.accessor("total_cost_usd", {
      header: "cost", cell: (info) => {
        const v = info.getValue();
        return v != null ? `$${v.toFixed(2)}` : "—";
      },
    }),
    helper.accessor("created_at", { header: "time", cell: (info) => fmtTime(info.getValue()) }),
    helper.display({
      id: "actions", header: "操作", cell: (info) => {
        const w = info.row.original;
        return w.status === "running" ? (
          <Button size="sm" variant="ghost" onClick={() => setPendingAction({ ws: w.name, kind: "cancel" })}>取消</Button>
        ) : (
          <Button size="sm" variant="ghost" className="text-red" onClick={() => setPendingAction({ ws: w.name, kind: "delete" })}>删除</Button>
        );
      },
    }),
  ], []);

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
      if (pendingAction.kind === "cancel") await cancelScan(pendingAction.ws);
      else await deleteWorkspace(pendingAction.ws);
      await refresh();
      setPendingAction(null);
    } finally {
      setBusy(false);
    }
  }

  const lastUpdatedStr = lastUpdated ? lastUpdated.toLocaleTimeString() : "—";

  return (
    <div className="space-y-4">
      {/* 工具栏 */}
      <div className="flex flex-wrap items-center gap-3">
        <Input
          placeholder="搜索 workspace..."
          value={globalFilter}
          onChange={(e) => setGlobalFilter(e.target.value)}
          className="max-w-xs"
        />
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger aria-label="status 筛选" className="w-32"><SelectValue placeholder="status" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">all status</SelectItem>
            <SelectItem value="running">running</SelectItem>
            <SelectItem value="completed">completed</SelectItem>
            <SelectItem value="failed">failed</SelectItem>
            <SelectItem value="killed">killed</SelectItem>
            <SelectItem value="crashed">crashed</SelectItem>
            <SelectItem value="interrupted">interrupted</SelectItem>
          </SelectContent>
        </Select>
        <Select value={typeFilter} onValueChange={setTypeFilter}>
          <SelectTrigger aria-label="type 筛选" className="w-32"><SelectValue placeholder="type" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">all type</SelectItem>
            <SelectItem value="whitebox">whitebox</SelectItem>
            <SelectItem value="blackbox">blackbox</SelectItem>
            <SelectItem value="correlation">correlation</SelectItem>
          </SelectContent>
        </Select>
        <Button variant="outline" size="sm" onClick={() => refresh()}>+ 新建扫描</Button>
        <div className="ml-auto flex items-center gap-3">
          <span className="text-xs text-muted-foreground">上次刷新 {lastUpdatedStr}</span>
          <Button variant="ghost" size="icon" aria-label="手动刷新" onClick={() => refresh()}>↻</Button>
        </div>
      </div>

      {error && <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-sm text-red">{error}</div>}

      {/* 表格 / 空态 / loading */}
      {loading && data.length === 0 ? (
        <div className="space-y-2">
          {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-8 w-full" />)}
        </div>
      ) : data.length === 0 ? (
        <Empty title="no workspaces" hint="新建一个扫描开始">
          <Button onClick={() => refresh()}>+ 新建扫描</Button>
        </Empty>
      ) : (
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((hg) => (
              <TableRow key={hg.id}>
                {hg.headers.map((h) => (
                  <TableHead key={h.id} onClick={h.column.getToggleSortingHandler()} className="cursor-pointer">
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
                          <span className="text-muted-foreground">无子白盒</span>
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
            <DialogTitle>{pendingAction?.kind === "cancel" ? "取消扫描" : "删除 workspace"}</DialogTitle>
            <DialogDescription>
              {pendingAction?.kind === "cancel"
                ? `取消扫描 ${pendingAction?.ws}？进度会丢失。`
                : `删除 workspace ${pendingAction?.ws}？目录和产物永久删除。`}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPendingAction(null)}>取消</Button>
            <Button variant="destructive" disabled={busy} onClick={doAction}>确认</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
