import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import { useWorkspaces } from "@/api/useWorkspaces";

/**
 * 顶栏「工作区」入口的三段跳转（IA 重设计 §2.3）：
 * 1) pinned 存在 -> /p/:pinned
 * 2) 无 pinned 但有归属 ws -> /p/:最近活跃 ws（latest_created_at 倒序首项）
 * 3) 无归属 ws -> / （Dashboard 自带空态）
 *
 * loading 期间不跳转（等 useWorkspaces 首次拉取完成避免误判空态）。
 */
export function WorkspacesEntry() {
  const { user } = useAuth();
  const { data, loading } = useWorkspaces();
  const nav = useNavigate();

  useEffect(() => {
    if (loading) return;
    const pinned = user?.pinned_workspace;
    if (pinned) {
      nav(`/p/${pinned}`, { replace: true });
      return;
    }
    if (data.length > 0) {
      const recent = [...data].sort(
        (a, b) => (b.latest_created_at ?? b.created_at) - (a.latest_created_at ?? a.created_at),
      )[0];
      nav(`/p/${recent.name}`, { replace: true });
      return;
    }
    nav("/", { replace: true });
  }, [user?.pinned_workspace, data, loading, nav]);

  return null;
}
