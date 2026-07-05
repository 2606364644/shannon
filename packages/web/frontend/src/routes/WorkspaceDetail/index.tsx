import { useEffect, useState } from "react";
import { Outlet, useParams, useLocation, useNavigate, Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/StatusBadge";
import { apiGet } from "@/api/client";
import type { SessionData } from "@/api/types";

const TABS = [
  { value: "overview", label: "概览" },
  { value: "report", label: "报告" },
  { value: "deliverables", label: "产物" },
  { value: "logs", label: "日志" },
  { value: "live", label: "实时" },
];

export default function WorkspaceDetail() {
  const { workspace } = useParams<{ workspace: string }>();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const current = pathname.split("/").pop() ?? "overview";
  const [meta, setMeta] = useState<SessionData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!workspace) return;
    setLoading(true);
    apiGet<SessionData>(`/workspaces/${workspace}`)
      .then((s) => { setMeta(s); setLoading(false); })
      .catch(() => { setMeta(null); setLoading(false); });
  }, [workspace]);

  const status = meta?.status ?? meta?.session?.status ?? "running";

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Link
          to="/workspaces"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-primary"
        >
          <ArrowLeft className="size-3.5" /> 返回列表
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-mono text-xl">{workspace}</h2>
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
        <TabsList>
          {TABS.map((t) => (
            <TabsTrigger key={t.value} value={t.value}>{t.label}</TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <div><Outlet /></div>
    </div>
  );
}
