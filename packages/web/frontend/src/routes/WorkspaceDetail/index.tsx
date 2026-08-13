import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Outlet, useParams, Link } from "react-router-dom";
import { ArrowLeft, Settings, FolderGit2, Pin, KeyRound, Globe } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/StatusBadge";
import { MemberManagerDialog } from "@/components/MemberManagerDialog";
import { WorkspaceSwitcher } from "@/components/WorkspaceSwitcher";
import { apiGet, ApiError, setPinnedWorkspace } from "@/api/client";
import type { SessionData } from "@/api/types";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { useAuth } from "@/auth/AuthContext";
import { toast } from "sonner";

/**
 * ws 概览布局（/p/:ws）：ws header（名 + latest_status + scan_count + 成员/仓库/settings 入口）
 * + Outlet（index=ScanList 扫描列表 / repos=ReposTab / settings=WsSettingsTab）。
 *
 * ws-scan 解耦（spec §12.7）：workspace 是容器，无「再次扫描」入口--只有「新建扫描」+
 * 扫描列表（在 ScanList）。scan 级 tab（overview/report/...）在 ScanDetail（/p/:ws/scans/:scanId）。
 *
 * header 的 latest_status/scan_count 取自 GET /workspaces/{ws} shim（返 latest scan payload +
 * scans[]）；Phase 1 未上线时旧 payload 无 scans[]，scan_count 缺失不显，不阻塞。
 */
export default function WorkspaceDetail() {
  const { t } = useTranslation();
  const { workspace } = useParams<{ workspace: string }>();
  const [meta, setMeta] = useState<SessionData | null>(null);
  const [scansCount, setScansCount] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (!workspace) return;
    setLoading(true);
    setNotFound(false);
    apiGet<SessionData & { scans?: unknown[] }>(`/workspaces/${workspace}`)
      .then((s) => {
        setMeta(s);
        setScansCount(Array.isArray(s.scans) ? s.scans.length : null);
        setLoading(false);
      })
      .catch((e) => {
        setMeta(null);
        setScansCount(null);
        setLoading(false);
        setNotFound(e instanceof ApiError && e.status === 404);
      });
  }, [workspace]);

  const { user, refreshUser } = useAuth();
  const isPinned = user?.pinned_workspace === workspace;

  async function onPin() {
    if (!workspace) return;
    try {
      await setPinnedWorkspace(workspace);
      await refreshUser();
      toast.success(t("workspaceDetail.pinPinned"));
    } catch (e) {
      toast.error(t("workspaceDetail.pinFailed", { error: e instanceof Error ? e.message : String(e) }));
    }
  }

  const status = meta?.status ?? meta?.session?.status;

  if (notFound) {
    return (
      <div className="space-y-4">
        <Link to="/workspaces-entry" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-primary">
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
          to="/workspaces-entry"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-primary"
        >
          <ArrowLeft className="size-3.5" /> {t("workspaceDetail.backToList")}
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-mono text-xl">{workspace}</h2>
          {/* 命令栏：所有操作按钮统一 h-9（outline 同层），不再 sm/icon 混用导致高低不一 */}
          <div className="flex flex-wrap items-center gap-2">
            <Button variant={isPinned ? "secondary" : "outline"} size="icon" onClick={onPin} title={t(isPinned ? "workspaceDetail.unpin" : "workspaceDetail.pin")}>
              <Pin className="size-4" />
            </Button>
            <WorkspaceSwitcher currentWorkspace={workspace} />
            {workspace && <MemberManagerDialog ws={workspace} />}
            {workspace && (
              <Button variant="outline" asChild>
                <Link to="repos" aria-label={t("workspaceDetail.tabs.repos")} title={t("workspaceDetail.tabs.repos")}>
                  <FolderGit2 className="size-4" /> {t("workspaceDetail.tabs.repos")}
                </Link>
              </Button>
            )}
            {workspace && (
              <Button variant="outline" asChild>
                <Link to="auth-profiles" aria-label={t("authProfiles.openLabel")} title={t("authProfiles.openLabel")}>
                  <KeyRound className="size-4" /> {t("authProfiles.openLabel")}
                </Link>
              </Button>
            )}
            {workspace && (
              <Button variant="outline" asChild>
                <Link to="host-profiles" aria-label={t("hostProfiles.openLabel")} title={t("hostProfiles.openLabel")}>
                  <Globe className="size-4" /> {t("hostProfiles.openLabel")}
                </Link>
              </Button>
            )}
            {workspace && (
              <Button variant="outline" size="icon" asChild>
                <Link to="settings" aria-label={t("wsConfig.openSettings")} title={t("wsConfig.openSettings")}>
                  <Settings className="size-4" />
                </Link>
              </Button>
            )}
          </div>
          {loading ? (
            <Skeleton className="h-5 w-40" />
          ) : (
            <>
              {status && <StatusBadge status={status} />}
              {meta?.scan_type && (
                <Badge variant="outline" className="font-mono">{meta.scan_type}</Badge>
              )}
              {scansCount != null && (
                <Badge variant="secondary" className="font-mono">
                  {t("workspaceDetail.scans.listTitle")} · {scansCount}
                </Badge>
              )}
            </>
          )}
        </div>
      </div>
      <ErrorBoundary><Outlet /></ErrorBoundary>
    </div>
  );
}
