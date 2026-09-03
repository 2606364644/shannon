import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { BranchCombobox } from "./BranchCombobox";
import { ApiError, checkoutRepo, pullRepo } from "@/api/client";
import type { Repo } from "@/api/types";
import { useRepos } from "@/api/useRepos";
import { useAuth } from "@/auth/AuthContext";

/** pull 后延迟刷新：202 返回时 pull 尚在跑，立即刷列表读不到终态（对齐 ReposTab）。 */
const PULL_REFRESH_DELAY_MS = 1500;

/** 扫描表单选中仓库后的快捷操作条（2026-09-03 仓库入口整合 C 段）：
 *  当前分支切换（checkout）+ 更新（pull）——免去跑去仓库管理页。
 *
 *  - linked 共享路径可写但 admin-only（spec 2026-09-04）：非 admin 整条不渲染。
 *  - upload 静态快照：不可 pull（无更新按钮），保留本地分支切换。
 *  - 刷新走 SWR 共享 key ["repos", ws]（两张表单的仓库下拉同缓存，一处刷新全局生效）。
 *  - 错误语义对齐 ReposTab：409=被扫描引用、422=分支不存在/dirty 冲突、pull 失败带 status。
 */
export function RepoQuickActions({ workspace, repo }: { workspace: string; repo: Repo }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const { refresh } = useRepos(workspace || undefined);
  const pullTimer = useRef<number | null>(null);

  if (repo.linked && user?.role !== "admin") return null;

  async function doPull() {
    try {
      await pullRepo(workspace, repo.name);
      toast.success(t("repos.updating", { name: repo.name }));
      if (pullTimer.current) window.clearTimeout(pullTimer.current);
      pullTimer.current = window.setTimeout(() => void refresh(), PULL_REFRESH_DELAY_MS);
    } catch (e) {
      if (e instanceof ApiError) {
        // 后端 detail（linked pull 失败/超时带 git 原文，spec 2026-09-04 §5.3）优先透出
        const detail = (e.body as { detail?: string } | null)?.detail;
        toast.error(detail ?? t("repos.errors.updateFailed", { status: e.status }));
      }
    }
  }

  async function doCheckout(branch: string) {
    try {
      await checkoutRepo(workspace, repo.name, branch);
      toast.success(t("repoDetail.checkoutSuccess", { branch }));
      await refresh();
    } catch (e) {
      if (e instanceof ApiError) {
        toast.error(
          e.status === 409
            ? t("repos.errors.inUse")
            : e.status === 422
              // 后端 detail 优先（dirty 冲突带 git 原文；分支不存在带分支名，spec 2026-09-04 §4.2）
              ? ((e.body as { detail?: string } | null)?.detail
                  ?? t("repoDetail.errors.branchNotFound", { branch }))
              : t("repoDetail.errors.checkoutFailed", { status: e.status }),
        );
      }
    }
  }

  const isUpload = repo.source?.kind === "upload";
  return (
    <div className="flex items-center gap-3 text-xs" data-testid="repo-quick-actions">
      <span className="text-muted-foreground">{t("scan.repo.currentBranch")}</span>
      <BranchCombobox ws={workspace} repo={repo.name} value={repo.source?.branch ?? null} onSwitch={doCheckout} />
      {!isUpload && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="ml-auto shrink-0 text-xs"
          data-testid="repo-pull-btn"
          onClick={() => void doPull()}
        >
          <RefreshCw className="size-3" />
          {t("scan.repo.updateBtn")}
        </Button>
      )}
    </div>
  );
}
