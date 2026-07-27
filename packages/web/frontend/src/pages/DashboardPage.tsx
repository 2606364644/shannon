import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/StatusBadge";
import { PageHeader } from "@/components/PageHeader";
import { StatRow } from "@/components/StatRow";
import { Badge } from "@/components/ui/badge";
import { ErrorState } from "@/components/ErrorState";
import { Empty } from "@/components/Empty";
import { useWorkspaces } from "@/api/useWorkspaces";
import type { Workspace } from "@/api/types";
import { fmtCost } from "@/utils/currency";

function isToday(unix: number | null | undefined): boolean {
  if (!unix) return false;
  const d = new Date(unix * 1000);
  const now = new Date();
  return d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth()
    && d.getDate() === now.getDate();
}

function sum<T>(arr: T[], pick: (t: T) => number | undefined): number {
  return arr.reduce((acc, x) => acc + (pick(x) ?? 0), 0);
}

function fmtMs(ms: number): string {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}h${m % 60}m`;
  if (m > 0) return `${m}m${s % 60}s`;
  return `${s}s`;
}

function fmtTime(unix?: number | null): string {
  if (!unix) return "—";
  return new Date(unix * 1000).toLocaleString();
}

export function DashboardPage() {
  const { t } = useTranslation();
  const { data, loading, error, refresh } = useWorkspaces();

  if (error && data.length === 0) {
    return <ErrorState message={t("dashboard.errors.loadFailed", { error })} onRetry={refresh} />;
  }
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

  const running = data.filter((w: Workspace) => w.status === "running");
  const completedToday = data.filter((w: Workspace) => w.status === "completed" && isToday(w.completed_at));
  const totalVulns = sum(data, (w: Workspace) => w.vuln_count);
  const totalCost = sum(data, (w: Workspace) => w.total_cost_usd);

  const recent = data
    .filter((w) => w.status !== "running")
    .slice()
    .sort((a, b) => (b.completed_at ?? b.created_at) - (a.completed_at ?? a.created_at))
    .slice(0, 8);

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
        { label: t("dashboard.stats.totalCost"), value: fmtCost(totalCost, data[0]?.cost_currency) },
      ]} />

      {running.length > 0 ? (
        <section className="space-y-2">
          <h2 className="font-semibold tracking-tight text-lg text-muted-foreground">{t("dashboard.runningTitle")}</h2>
          <div className="grid gap-3 md:grid-cols-2">
            {running.map((w) => (
              <Link key={w.name} to={`/p/${w.name}/live`} className="block">
                <Card className="transition-colors hover:border-primary">
                  <CardContent className="space-y-1 p-4 font-mono text-sm">
                    <div className="flex items-center justify-between">
                      <StatusBadge status={w.status} />
                      <Badge variant="outline">{w.scan_type}</Badge>
                    </div>
                    <div className="text-base text-foreground">{w.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {w.total_cost_usd != null ? fmtCost(w.total_cost_usd, w.cost_currency) : "—"}{" · "}
                      {w.total_duration_ms ? fmtMs(w.total_duration_ms) : "—"}
                    </div>
                    <div className="text-xs text-primary">{t("dashboard.viewLive")}</div>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        </section>
      ) : (
        <p className="text-sm text-muted-foreground">{t("dashboard.noRunning")}</p>
      )}

      {recent.length > 0 && (
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold tracking-tight text-lg text-muted-foreground">{t("dashboard.recentTitle")}</h2>
            <Link to="/workspaces" className="text-sm text-primary hover:underline">{t("dashboard.viewAll")}</Link>
          </div>
          <Card>
            <CardContent className="divide-y divide-border p-0">
              {recent.map((w) => (
                <Link key={w.name} to={`/p/${w.name}`} className="flex flex-wrap items-center gap-3 p-3 font-mono text-sm hover:bg-accent">
                  <StatusBadge status={w.status} />
                  <span className="text-foreground">{w.name}</span>
                  <Badge variant="outline">{w.scan_type}</Badge>
                  <span className="text-muted-foreground">{w.vuln_count ?? 0} vuln</span>
                  <span className="text-muted-foreground">{w.total_cost_usd != null ? fmtCost(w.total_cost_usd, w.cost_currency) : "—"}</span>
                  <span className="ml-auto text-xs text-muted-foreground">{fmtTime(w.completed_at ?? w.created_at)}</span>
                </Link>
              ))}
            </CardContent>
          </Card>
        </section>
      )}
    </div>
  );
}
