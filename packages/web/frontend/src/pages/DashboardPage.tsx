import { Link } from "react-router-dom";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { ErrorState } from "@/components/ErrorState";
import { Empty } from "@/components/Empty";
import { useWorkspaces } from "@/api/useWorkspaces";
import type { Workspace } from "@/api/types";

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
  const { data, loading, error, refresh } = useWorkspaces();

  if (error && data.length === 0) {
    return <ErrorState message={`Dashboard 加载失败:${error}`} onRetry={refresh} />;
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
      <Empty title="还没有扫描" hint="新建一个扫描开始">
        <Link to="/scan/new"><Button>+ 新建扫描</Button></Link>
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
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="font-serif text-2xl">Shannon</h1>
        <Link to="/scan/new"><Button>+ 新建扫描</Button></Link>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4" role="group" aria-label="汇总">
        <Card><CardContent className="p-4">
          <div className="text-xs text-muted-foreground">运行中</div>
          <div className="font-mono text-2xl text-cyan">{running.length}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs text-muted-foreground">今日完成</div>
          <div className="font-mono text-2xl text-green">{completedToday.length}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs text-muted-foreground">累计漏洞</div>
          <div className="font-mono text-2xl">{totalVulns}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs text-muted-foreground">累计 cost</div>
          <div className="font-mono text-2xl">${totalCost.toFixed(2)}</div>
        </CardContent></Card>
      </div>

      {running.length > 0 ? (
        <section className="space-y-2">
          <h2 className="font-serif text-lg text-muted-foreground">正在运行</h2>
          <div className="grid gap-3 md:grid-cols-2">
            {running.map((w) => (
              <Link key={w.name} to={`/p/${w.name}/live`} className="block">
                <Card className="transition-color hover:border-primary">
                  <CardContent className="space-y-1 p-4 font-mono text-sm">
                    <div className="flex items-center justify-between">
                      <StatusBadge status={w.status} />
                      <Badge variant="outline">{w.scan_type}</Badge>
                    </div>
                    <div className="text-base text-foreground">{w.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {w.total_cost_usd != null ? `$${w.total_cost_usd.toFixed(2)}` : "—"}{" · "}
                      {w.total_duration_ms ? fmtMs(w.total_duration_ms) : "—"}
                    </div>
                    <div className="text-xs text-primary">查看实时 →</div>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        </section>
      ) : (
        <p className="text-sm text-muted-foreground">当前无运行中扫描</p>
      )}

      {recent.length > 0 && (
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="font-serif text-lg text-muted-foreground">最近扫描</h2>
            <Link to="/workspaces" className="text-sm text-primary hover:underline">查看全部 →</Link>
          </div>
          <Card>
            <CardContent className="divide-y divide-border p-0">
              {recent.map((w) => (
                <Link key={w.name} to={`/p/${w.name}`} className="flex flex-wrap items-center gap-3 p-3 font-mono text-sm hover:bg-accent">
                  <StatusBadge status={w.status} />
                  <span className="text-foreground">{w.name}</span>
                  <Badge variant="outline">{w.scan_type}</Badge>
                  <span className="text-muted-foreground">{w.vuln_count ?? 0} vuln</span>
                  <span className="text-muted-foreground">{w.total_cost_usd != null ? `$${w.total_cost_usd.toFixed(2)}` : "—"}</span>
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
