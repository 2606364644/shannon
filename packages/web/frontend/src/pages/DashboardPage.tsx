import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/StatusBadge";
import { PageHeader } from "@/components/PageHeader";
import { StatRow } from "@/components/StatRow";
import { Badge } from "@/components/ui/badge";
import { Empty } from "@/components/Empty";
import { ScanFilters, DEFAULT_SCAN_FILTERS, useScanFilters } from "@/components/ScanFilters";
import { listAllScans } from "@/api/client";
import type { ScanSummary } from "@/api/types";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fmtCost } from "@/utils/currency";
import { useAsync } from "@/lib/useAsync";

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

export function DashboardPage() {
  const { t } = useTranslation();
  // 跨 ws 扫描聚合(IA 重设计 §3,GET /api/scans)。每项注入 workspace 字段供表格「归属工作区」列消费。
  const { data, loading } = useAsync(listAllScans, []);
  const [filters, setFilters] = useState(DEFAULT_SCAN_FILTERS);
  const { filtered } = useScanFilters(data, filters);

  const running = filtered.filter((s) => s.is_running || s.status === "running");
  const completedToday = filtered.filter((s) => s.status === "completed" && isToday(s.completed_at));
  const totalVulns = filtered.reduce((a, s) => a + (s.vuln_count ?? 0), 0);
  const totalCost = filtered.reduce((a, s) => a + (s.total_cost_usd ?? 0), 0);
  const currency = filtered.find((s) => s.cost_currency)?.cost_currency;

  if (loading && data.length === 0) {
    return (
      <div className="space-y-2">
        {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-8 w-full" />)}
      </div>
    );
  }
  if (data.length === 0) {
    return (
      <Empty title={t("dashboard.empty.title")} hint={t("dashboard.empty.hint")}>
        <Button variant="cta" asChild><Link to="/scan/new">{t("dashboard.newScan")}</Link></Button>
      </Empty>
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title={t("dashboard.title")}
        subtitle={t("dashboard.subtitle")}
        action={<Button variant="cta" asChild><Link to="/scan/new">{t("dashboard.newScan")}</Link></Button>}
      />
      <StatRow stats={[
        { label: t("dashboard.stats.running"), value: running.length, tone: "cyan" },
        { label: t("dashboard.stats.completedToday"), value: completedToday.length, tone: "green" },
        { label: t("dashboard.stats.totalVulns"), value: totalVulns },
        { label: t("dashboard.stats.totalCost"), value: fmtCost(totalCost, currency) },
      ]} />

      {running.length > 0 && (
        <section className="space-y-2">
          <h2 className="font-semibold tracking-tight text-lg text-muted-foreground">{t("dashboard.runningTitle")}</h2>
          <div className="grid gap-3 md:grid-cols-2">
            {running.map((s: ScanSummary) => (
              <Link key={s.scan_id} to={`/p/${s.workspace}/scans/${s.scan_id}/live`} className="block">
                <Card className="transition-colors hover:border-primary">
                  <CardContent className="space-y-1 p-4 font-mono text-sm">
                    <div className="flex items-center justify-between">
                      <StatusBadge status={s.status} />
                      <Badge variant="outline">{s.scan_type}</Badge>
                    </div>
                    <div className="text-base text-foreground">{s.scan_id}</div>
                    <div className="text-xs text-muted-foreground">{t("dashboard.scanTable.workspace")}: {s.workspace}</div>
                    <div className="text-xs text-primary">{t("dashboard.viewLive")}</div>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        </section>
      )}

      <ScanFilters value={filters} onChange={setFilters} />

      <Card className="overflow-hidden p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("dashboard.scanTable.status")}</TableHead>
              <TableHead>{t("dashboard.scanTable.scanId")}</TableHead>
              <TableHead>{t("dashboard.scanTable.workspace")}</TableHead>
              <TableHead>{t("dashboard.scanTable.type")}</TableHead>
              <TableHead>{t("dashboard.scanTable.vulns")}</TableHead>
              <TableHead>{t("dashboard.scanTable.cost")}</TableHead>
              <TableHead>{t("dashboard.scanTable.time")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((s: ScanSummary) => (
              <TableRow key={s.scan_id}>
                <TableCell><StatusBadge status={s.status} /></TableCell>
                <TableCell className="font-mono"><Link to={`/p/${s.workspace}/scans/${s.scan_id}`} className="hover:text-primary">{s.scan_id}</Link></TableCell>
                <TableCell className="font-mono"><Link to={`/p/${s.workspace}`} className="hover:text-primary">{s.workspace}</Link></TableCell>
                <TableCell><Badge variant="outline">{s.scan_type}</Badge></TableCell>
                <TableCell>{s.vuln_count ?? 0}</TableCell>
                <TableCell>{s.total_cost_usd != null ? fmtCost(s.total_cost_usd, s.cost_currency) : "-"}</TableCell>
                <TableCell className="text-xs text-muted-foreground">{fmtTime(s.created_at)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
      {filtered.length === 0 && <p className="text-sm text-muted-foreground">{t("workspaceDetail.scans.noMatch")}</p>}
    </div>
  );
}
