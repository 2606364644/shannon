import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Outlet, useParams, useLocation, useNavigate, Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/StatusBadge";
import { MemberManagerDialog } from "@/components/MemberManagerDialog";
import { apiGet, ApiError } from "@/api/client";
import type { SessionData } from "@/api/types";
import { ErrorBoundary } from "@/components/ErrorBoundary";

const TABS = [
  { value: "overview", labelKey: "workspaceDetail.tabs.overview" },
  { value: "report", labelKey: "workspaceDetail.tabs.report" },
  { value: "deliverables", labelKey: "workspaceDetail.tabs.deliverables" },
  { value: "logs", labelKey: "workspaceDetail.tabs.logs" },
  { value: "live", labelKey: "workspaceDetail.tabs.live" },
] as const;

export default function WorkspaceDetail() {
  const { t } = useTranslation();
  const { workspace } = useParams<{ workspace: string }>();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const current = pathname.split("/").pop() ?? "overview";
  const [meta, setMeta] = useState<SessionData | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (!workspace) return;
    setLoading(true);
    setNotFound(false);
    apiGet<SessionData>(`/workspaces/${workspace}`)
      .then((s) => { setMeta(s); setLoading(false); })
      .catch((e) => {
        setMeta(null);
        setLoading(false);
        // 404 = 工作区不存在/已删：显明确错误态，不降级成 running 误导。
        // 其余错误（500 等）保持现降级（显名 + tabs + 默认 running），不阻塞浏览。
        setNotFound(e instanceof ApiError && e.status === 404);
      });
  }, [workspace]);

  const status = meta?.status ?? meta?.session?.status ?? "running";

  if (notFound) {
    return (
      <div className="space-y-4">
        <Link to="/workspaces" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-primary">
          <ArrowLeft className="size-3.5" /> {t("workspaceDetail.backToList")}
        </Link>
        <div className="rounded-md border border-yellow/40 bg-card p-6 text-sm">
          <h2 className="font-mono text-xl mb-2">{workspace}</h2>
          <p className="text-muted-foreground">
            {t("workspaceDetail.notFound.message")}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Link
          to="/workspaces"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-primary"
        >
          <ArrowLeft className="size-3.5" /> {t("workspaceDetail.backToList")}
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-mono text-xl">{workspace}</h2>
          {workspace && <MemberManagerDialog ws={workspace} />}
          {loading ? (
            <Skeleton className="h-5 w-40" />
          ) : (
            <>
              <StatusBadge status={status} />
              {meta?.scan_type && (
                <Badge variant="outline" className="font-mono">{meta.scan_type}</Badge>
              )}
              {meta?.repo_path && (
                <span className="font-mono text-sm text-muted-foreground">{meta.repo_path}</span>
              )}
            </>
          )}
        </div>
      </div>
      <Tabs value={current} onValueChange={(v) => navigate(v)}>
        <div data-testid="wd-tabs-sticky" className="sticky top-12 z-30 print:static">
          <TabsList>
            {TABS.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value}>{t(tab.labelKey)}</TabsTrigger>
            ))}
          </TabsList>
        </div>
      </Tabs>
      <div><ErrorBoundary key={current}><Outlet /></ErrorBoundary></div>
    </div>
  );
}
