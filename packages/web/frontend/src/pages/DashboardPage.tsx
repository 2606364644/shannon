import { Link } from "react-router-dom";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
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

      {/* Task 5 在此插入 running 卡片墙 + 最近扫描区 */}
    </div>
  );
}
