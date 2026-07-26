import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/auth/AuthContext";
import { createWorkspace } from "@/api/client";

/**
 * Admin-only「新建 workspace」dialog。补 P1 §6 前端缺口：
 * P1 已有后端 POST /api/workspaces（admin-only），缺前端入口。
 *
 * 错误处理对齐 MemberManagerDialog：try/catch + toast.error + busy 锁按钮；
 * 失败时弹窗保留（用户可重试或取消），不静默吞错也不卡死。
 */
export function CreateWorkspaceDialog({ onCreated }: { onCreated: (name: string) => void }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  if (user?.role !== "admin") return null;

  async function onCreate() {
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    try {
      const r = await createWorkspace(trimmed);
      setOpen(false);
      setName("");
      onCreated(r.name);
    } catch (e) {
      // 409 重名 / 422 校验 / 网络：toast 错误，弹窗保留以重试。
      toast.error(t("workspace.create.failed", { error: e instanceof Error ? e.message : String(e) }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => {
      // busy 时禁止 ESC/点遮罩关闭，避免创建中途丢失状态。
      if (!busy) {
        setOpen(o);
        if (!o) setName("");
      }
    }}>
      <DialogTrigger asChild>
        <Button size="sm">{t("workspace.create.button")}</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("workspace.create.title")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="ws-name">{t("workspace.create.name")}</Label>
          <Input
            id="ws-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("workspace.create.placeholder")}
            disabled={busy}
          />
          <Button onClick={onCreate} disabled={!name.trim() || busy}>
            {busy ? t("workspace.create.submitting") : t("workspace.create.submit")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
