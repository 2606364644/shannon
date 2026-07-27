import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { useAuth } from "@/auth/AuthContext";
import { getMembers, addMember, removeMember } from "@/api/members";
import { ApiError } from "@/api/client";
import type { Member } from "@/api/members";

export function MemberManagerDialog({ ws }: { ws: string }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [members, setMembers] = useState<Member[]>([]);
  const [picked, setPicked] = useState("");

  useEffect(() => {
    if (!user) return;
    getMembers(ws).then((r) => setMembers(r.members)).catch(() => {});
  }, [ws, user]);

  const myRole = user?.role === "admin" ? "admin" : members.find((m) => m.user_id === user?.id)?.role;
  const canManage = myRole === "admin" || myRole === "manager";
  if (!canManage) return null;
  if (user?.role !== "admin" && !members.some((m) => m.user_id === user?.id)) return null;

  async function onAdd() {
    if (!picked) return;
    try {
      await addMember(ws, picked, "member");
      setPicked("");
      setMembers((await getMembers(ws)).members);
    } catch (e) {
      // 404=用户不存在（GET /users 收紧后 manager 拉不到列表，改为手输）；其余按 addFailed
      const status = e instanceof ApiError ? e.status : 0;
      toast.error(status === 404 ? t("members.input.notFound") : t("members.addFailed"));
    }
  }
  async function onRemove(username: string) {
    try {
      await removeMember(ws, username);
      setMembers((await getMembers(ws)).members);
    } catch {
      toast.error(t("members.removeFailed"));
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" onClick={() => setOpen(true)} data-testid="member-manager">{t("members.manage")}</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader><DialogTitle>{t("members.title")}</DialogTitle></DialogHeader>
        <ul className="space-y-1">
          {members.map((m) => (
            <li key={m.user_id} className="flex items-center justify-between rounded-md border border-border bg-secondary/40 px-3 py-2 text-sm">
              <span className="flex items-center gap-2">{m.username} <Badge variant="outline" className="font-mono text-xs text-muted-foreground">{t(`members.${m.role}`)}</Badge></span>
              <Button variant="ghost" size="sm" onClick={() => onRemove(m.username)}>{t("members.remove")}</Button>
            </li>
          ))}
        </ul>
        <div className="flex items-center gap-2">
          <Input value={picked} onChange={(e) => setPicked(e.target.value)}
                 placeholder={t("members.input.placeholder")} className="flex-1" />
          <Button variant="outline" onClick={onAdd} disabled={!picked}>{t("members.add")}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
