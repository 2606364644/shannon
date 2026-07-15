import { useEffect, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import i18n from "@/i18n";
import { getRepo, pullRepo, checkoutRepo, ApiError } from "@/api/client";
import type { RepoDetail } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { CloneProgress } from "@/components/CloneProgress";

export function RepoDetailPage() {
  // 路由用 splat /repos/* 承载 group/repo 名（含 '/'）；useParams["*"] 取整段
  const params = useParams();
  const name = params["*"] ?? "";
  const nav = useNavigate();
  const { t } = useTranslation();
  const [repo, setRepo] = useState<RepoDetail | null>(null);
  const [error, setError] = useState<boolean>(false);
  const [branch, setBranch] = useState("");

  useEffect(() => {
    let cancelled = false;
    getRepo(name)
      .then((res) => { if (!cancelled) setRepo(res); })
      .catch(() => { if (!cancelled) { setError(true); toast.error(i18n.t("repoDetail.errors.loadFailed")); } });
    return () => { cancelled = true; };
  }, [name]);

  async function doCheckout() {
    if (!branch.trim()) return;
    try {
      await checkoutRepo(name, branch.trim());
      toast.success(t("repoDetail.checkoutSuccess", { branch: branch.trim() }));
      setRepo(await getRepo(name));
    } catch (e) {
      if (e instanceof ApiError) toast.error(e.status === 422 ? t("repoDetail.errors.branchNotFound") : t("repoDetail.errors.checkoutFailed", { status: e.status }));
      else { toast.error(t("repoDetail.errors.checkoutNetwork")); console.error("doCheckout failed:", e); }
    }
  }

  async function doPull() {
    try {
      await pullRepo(name);
      toast.success(t("repoDetail.pullStarted"));
      setRepo(await getRepo(name));
    } catch (e) {
      if (e instanceof ApiError) toast.error(t("repos.errors.updateFailed", { status: e.status }));
      else { toast.error(t("repoDetail.errors.updateNetwork")); console.error("doPull failed:", e); }
    }
  }

  if (!repo) {
    if (error) {
      return (
        <div className="space-y-4">
          <div className="border border-destructive p-4 text-sm text-destructive">
            {t("repoDetail.loadErrorState")}
          </div>
          <Link to="/repos" className="text-sm text-muted-foreground hover:underline">{t("repoDetail.backToList")}</Link>
        </div>
      );
    }
    return <div className="text-sm text-muted-foreground">{t("common.loading")}</div>;
  }

  const busy = repo.state === "cloning" || repo.state === "pulling";

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Link to="/repos" className="text-sm text-muted-foreground hover:underline">{t("repoDetail.backToRepos")}</Link>
        <h1 className="text-xl font-semibold tracking-tight">{repo.name}</h1>
        <span title={repo.state} className={repo.state === "ready" ? "text-green text-sm" : repo.state === "failed" ? "text-destructive text-sm" : "text-muted-foreground text-sm"}>
          {t(`repos.states.${repo.state}`, { defaultValue: repo.state })}
        </span>
      </div>

      <div className="flex gap-2">
        <Button onClick={() => nav(`/scan/new?repo=${encodeURIComponent(name)}`)} disabled={repo.state !== "ready"}>
          {t("repoDetail.scanBtn")}
        </Button>
        <Button variant="outline" onClick={() => void doPull()}>
          {t("repoDetail.pullBtn")}
        </Button>
      </div>

      {busy && <CloneProgress name={name} />}
      {repo.state === "stale" && (
        <div className="border border-border p-3 text-sm text-yellow">{t("repoDetail.staleHint")}</div>
      )}

      <Card>
        <CardContent className="p-4 space-y-1 text-sm">
        <div>{t("repoDetail.meta.sourceRow", { value: repo.source?.url ?? repo.source?.kind ?? "-" })}</div>
        <div>{t("repoDetail.meta.branchRow", { branch: repo.source?.branch ?? "-", commit: repo.source?.commit ? `@ ${repo.source.commit.slice(0, 8)}` : "" })}</div>
        <div>{t("repoDetail.meta.clonedRow", { clonedAt: repo.cloned_at ?? "-", lastUpdate: repo.last_pull_at ?? "-" })}</div>
        {repo.last_error && <div className="text-destructive">{t("repoDetail.meta.errorRow", { error: repo.last_error })}</div>}
        </CardContent>
      </Card>

      <div className="flex gap-2">
        <Input value={branch} onChange={(e) => setBranch(e.target.value)} placeholder={t("repoDetail.branchPlaceholder")} />
        <Button variant="outline" onClick={doCheckout}>{t("repoDetail.checkoutBtn")}</Button>
      </div>

      {repo.recent_events && repo.recent_events.length > 0 && (
        <Card>
          <CardContent className="p-4">
          <div className="mb-2 text-sm font-medium">{t("repoDetail.cloneHistoryTitle")}</div>
          <ul className="space-y-1 text-sm text-muted-foreground">
            {repo.recent_events.slice(-10).map((e, i) => (
              <li key={(e as { ts?: string }).ts ?? `evt-${i}`}>{JSON.stringify(e)}</li>
            ))}
          </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
