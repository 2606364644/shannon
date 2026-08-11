// 认证档案库 页面(ws-child tab 内容)。范式镜像 UsersPage.tsx
// (refresh / loading / error / Card+Table / dialog 挂载) + ReposTab(useParams ws-scoped)。
// 结构: 档案列表 + 新建/编辑/删除 + 凭据行(占位, Task 12 扩展)。
import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Trash2, Pencil, Copy, FlaskConical } from "lucide-react";
import { listAuthProfiles, deleteAuthProfile, forkProfile } from "@/api/authProfiles";
import { ApiError } from "@/api/client";
import type { AuthProfile } from "@/api/types";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip";
import { CopyButton } from "@/components/CopyButton";
import { AuthProfileDialog } from "@/components/AuthProfileDialog";

export function AuthProfilesPage() {
  const { t } = useTranslation();
  const { workspace } = useParams<{ workspace: string }>();
  const nav = useNavigate();
  const [profiles, setProfiles] = useState<AuthProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<AuthProfile | null>(null);
  const [delTarget, setDelTarget] = useState<AuthProfile | null>(null);

  async function refresh() {
    if (!workspace) return;
    setLoading(true); setError(null);
    try {
      setProfiles(await listAuthProfiles(workspace));
    } catch {
      setError(t("authProfiles.createFailed"));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { void refresh(); }, [workspace]); // eslint-disable-line react-hooks/exhaustive-deps

  async function onDelete() {
    if (!workspace || !delTarget) return;
    try {
      await deleteAuthProfile(workspace, delTarget.id);
      toast.success(t("authProfiles.deleted"));
      setDelTarget(null);
      void refresh();
    } catch {
      toast.error(t("authProfiles.createFailed"));
    }
  }

  async function onFork(p: AuthProfile) {
    if (!workspace) return;
    try {
      await forkProfile(workspace, p.id);
      toast.success(t("authProfiles.forkSuccess"));
      void refresh();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        toast.info(t("authProfiles.forkAlready"));
      } else {
        toast.error(t("authProfiles.forkFailed"));
      }
    }
  }

  if (!workspace) return null;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-medium">{t("authProfiles.title")}</h3>
        <Button variant="cta" onClick={() => setCreateOpen(true)}>
          {t("authProfiles.create")}
        </Button>
      </div>
      {loading ? <Skeleton className="h-20 w-full" />
       : error ? <div className="text-sm text-destructive">{error}</div>
       : profiles.length === 0 ? <Card className="p-6 text-sm text-muted-foreground">{t("authProfiles.empty")}</Card>
       : <TooltipProvider><Card><Table className="table-fixed"><TableHeader><TableRow>
            {/* 列宽重平衡(table-fixed 恰留 login_url 一列弹性吸收余量):
                name w-64 锚点加宽(原 w-56 挤); credentials w-56 收紧(原弹性列吞空白);
                login_url 弹性 + truncate + Tooltip + hover 复制按钮(对齐 ReposTab 长URL范式)。 */}
            <TableHead className="w-64">{t("authProfiles.name")}</TableHead>
            <TableHead>{t("authProfiles.loginUrl")}</TableHead>
            <TableHead className="w-56">{t("authProfiles.credentials")}</TableHead>
            <TableHead className="w-28"></TableHead></TableRow></TableHeader>
            <TableBody>{profiles.map((p) => (
              <TableRow key={p.id} className="group transition-colors hover:bg-muted/40">
                {/* table-fixed + max-w-0 + break-all: 档案名是身份标识, 完整换行呈现(不靠 hover);
                    在连字符/任意处自然折行, 短名不受影响。login_url 才走 truncate。 */}
                <TableCell className="max-w-0">
                  <div className="flex items-start gap-2">
                    <span className="min-w-0 break-all font-mono text-sm leading-snug" title={p.name}>{p.name}</span>
                    {p.scope === "system" && (
                      <span
                        className="mt-px shrink-0 inline-flex items-center rounded-full border border-border bg-muted/40 px-2 py-0.5 text-[10.5px] font-semibold text-muted-foreground"
                        title={t("authProfiles.systemHint")}
                      >
                        {t("authProfiles.systemBadge")}
                      </span>
                    )}
                  </div>
                </TableCell>
                {/* 登录地址: 长 URL truncate + Tooltip(全 URL break-all) + hover 浮出复制按钮。
                    范式对齐 ReposTab 来源列——reference data 紧凑呈现 + 可复制。 */}
                <TableCell className="max-w-0">
                  <div className="flex items-center gap-1">
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground">
                          {p.login_url}
                        </span>
                      </TooltipTrigger>
                      <TooltipContent className="max-w-md break-all">{p.login_url}</TooltipContent>
                    </Tooltip>
                    <CopyButton
                      value={p.login_url}
                      ariaLabel={t("authProfiles.copyUrlAria", { name: p.name })}
                      className="shrink-0 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100"
                    />
                  </div>
                </TableCell>
                {/* 凭据列: 紧凑 chip 行(状态色点 + 角色·用户名), flex nowrap 不换行, 多角色整齐横排。
                    点 chip → window.open 新标签页打开该凭据的认证过程页(测试登录 + 回看), 列表保持轻量。 */}
                <TableCell className="max-w-0">
                  <div className="flex w-full items-center gap-1.5 overflow-x-auto pb-1">
                    {p.credentials.map((c) => {
                      const cst = c.verify_status?.state ?? "unverified";
                      const dotCls = cst === "success" ? "text-green" : cst === "failed" ? "text-red" : cst === "running" ? "text-blue" : "text-yellow";
                      const dot = cst === "success" ? "✓" : cst === "failed" ? "✗" : "●";
                      return (
                        <button
                          key={c.id}
                          type="button"
                          onClick={() => window.open(
                            `/p/${workspace}/auth-profiles/${p.id}/credentials/${c.id}`,
                            "_blank", "noopener,noreferrer",
                          )}
                          className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border bg-muted/30 px-2 py-1 text-xs transition-colors hover:border-primary/50 hover:bg-muted/70"
                          title={t("authProfiles.process.openHint", { role: c.role, user: c.username })}
                        >
                          <span aria-hidden className={`font-semibold ${dotCls}`}>{dot}</span>
                          <span className="font-mono">{c.role}·{c.username}</span>
                        </button>
                      );
                    })}
                  </div>
                </TableCell>
                {/* 操作列: flex + justify-end + gap-1 让编辑/删除(或 fork)横排并列, 不再折成一列。 */}
                <TableCell>
                  <div className="flex items-center justify-end gap-1">
                    {/* 档案级测试登录：跳档案测试页（多选角色 → 串行逐个独立验证）。所有档案(system/ws)都有。 */}
                    <Button variant="ghost" size="icon" aria-label={t("authProfiles.test")}
                      onClick={() => nav(`/p/${workspace}/auth-profiles/${p.id}`)}>
                      <FlaskConical className="size-4" />
                    </Button>
                    {p.scope === "system" && (
                      <Button variant="ghost" size="icon" aria-label={t("authProfiles.forkLabel")} onClick={() => onFork(p)}>
                        <Copy className="size-4" />
                      </Button>
                    )}
                    {p.scope !== "system" && (
                      <>
                        <Button variant="ghost" size="icon" aria-label={t("authProfiles.edit")} onClick={() => setEditTarget(p)}>
                          <Pencil className="size-4" />
                        </Button>
                        <Button variant="ghost" size="icon" aria-label={t("authProfiles.delete")} onClick={() => setDelTarget(p)}>
                          <Trash2 className="size-4" />
                        </Button>
                      </>
                    )}
                  </div>
                </TableCell>
              </TableRow>))}</TableBody></Table></Card></TooltipProvider>}

      <AuthProfileDialog ws={workspace} open={createOpen} onOpenChange={setCreateOpen} onSaved={refresh} />
      {editTarget && (
        <AuthProfileDialog
          ws={workspace}
          open
          onOpenChange={(o) => !o && setEditTarget(null)}
          onSaved={() => { setEditTarget(null); void refresh(); }}
          editing={editTarget}
        />
      )}
      <Dialog open={!!delTarget} onOpenChange={(o) => !o && setDelTarget(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t("authProfiles.delete")}</DialogTitle></DialogHeader>
          <p className="text-sm text-destructive">{delTarget?.name}</p>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDelTarget(null)}>{t("common.cancel")}</Button>
            <Button variant="destructive" onClick={onDelete}>{t("authProfiles.delete")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
