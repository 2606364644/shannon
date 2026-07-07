import { useEffect, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { toast } from "sonner";
import { getRepo, pullRepo, checkoutRepo, ApiError } from "@/api/client";
import type { RepoDetail } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CloneProgress } from "@/components/CloneProgress";

export function RepoDetailPage() {
  // 路由用 splat /repos/* 承载 group/repo 名（含 '/'）；useParams["*"] 取整段
  const params = useParams();
  const name = params["*"] ?? "";
  const nav = useNavigate();
  const [repo, setRepo] = useState<RepoDetail | null>(null);
  const [error, setError] = useState<boolean>(false);
  const [branch, setBranch] = useState("");

  useEffect(() => {
    let cancelled = false;
    getRepo(name)
      .then((res) => { if (!cancelled) setRepo(res); })
      .catch(() => { if (!cancelled) { setError(true); toast.error("加载失败"); } });
    return () => { cancelled = true; };
  }, [name]);

  async function doCheckout() {
    if (!branch.trim()) return;
    try {
      await checkoutRepo(name, branch.trim());
      toast.success(`已切换到 ${branch.trim()}`);
      setRepo(await getRepo(name));
    } catch (e) {
      if (e instanceof ApiError) toast.error(e.status === 422 ? "分支不存在" : `切换失败（${e.status}）`);
      else { toast.error("切换失败（网络错误）"); console.error("doCheckout failed:", e); }
    }
  }

  async function doPull() {
    try {
      await pullRepo(name);
      toast.success("更新中");
      setRepo(await getRepo(name));
    } catch (e) {
      if (e instanceof ApiError) toast.error(`更新失败（${e.status}）`);
      else { toast.error("更新失败（网络错误）"); console.error("doPull failed:", e); }
    }
  }

  if (!repo) {
    if (error) {
      return (
        <div className="space-y-4">
          <div className="border border-destructive bg-card p-4 text-sm text-destructive">
            仓库加载失败，可能不存在或已被删除。
          </div>
          <Link to="/repos" className="text-sm text-muted-foreground hover:underline">← 返回仓库列表</Link>
        </div>
      );
    }
    return <div className="text-sm text-muted-foreground">加载中…</div>;
  }

  const busy = repo.state === "cloning" || repo.state === "pulling";

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Link to="/repos" className="text-sm text-muted-foreground hover:underline">← 仓库</Link>
        <h1 className="font-semibold tracking-tight text-lg">{repo.name}</h1>
        <span className={repo.state === "ready" ? "text-green text-sm" : repo.state === "failed" ? "text-destructive text-sm" : "text-muted-foreground text-sm"}>
          {repo.state}
        </span>
      </div>

      <div className="flex gap-2">
        <Button onClick={() => nav(`/scan/new?repo=${encodeURIComponent(name)}`)} disabled={repo.state !== "ready"}>
          发起扫描
        </Button>
        <Button variant="outline" onClick={() => void doPull()}>
          更新 pull
        </Button>
      </div>

      {busy && <CloneProgress name={name} />}
      {repo.state === "stale" && (
        <div className="border border-border bg-card p-3 text-sm text-yellow">⚠ 上次 clone 未完成，建议重新添加或更新。</div>
      )}

      <div className="border border-border bg-card p-4 space-y-1 text-sm">
        <div>来源：{repo.source?.url ?? repo.source?.kind ?? "-"}</div>
        <div>分支：{repo.source?.branch ?? "-"} {repo.source?.commit ? `@ ${repo.source.commit.slice(0, 8)}` : ""}</div>
        <div>clone 于：{repo.cloned_at ?? "-"} · 最后更新：{repo.last_pull_at ?? "-"}</div>
        {repo.last_error && <div className="text-destructive">错误：{repo.last_error}</div>}
      </div>

      <div className="flex gap-2">
        <Input value={branch} onChange={(e) => setBranch(e.target.value)} placeholder="切换分支" />
        <Button variant="outline" onClick={doCheckout}>checkout</Button>
      </div>

      {repo.recent_events && repo.recent_events.length > 0 && (
        <div className="border border-border bg-card p-4">
          <div className="mb-2 text-sm font-medium">clone 历史（最近）</div>
          <ul className="space-y-1 text-xs text-muted-foreground">
            {repo.recent_events.slice(-10).map((e, i) => (
              <li key={(e as { ts?: string }).ts ?? `evt-${i}`}>{JSON.stringify(e)}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
