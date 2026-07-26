import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAuth } from "@/auth/AuthContext";
import { getMembers, addMember, removeMember, listUsers } from "@/api/members";
import type { Member, UserLite } from "@/api/members";

export function MemberManagerDialog({ ws }: { ws: string }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [members, setMembers] = useState<Member[]>([]);
  const [users, setUsers] = useState<UserLite[]>([]);
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
    await addMember(ws, picked, "member");
    setPicked("");
    setMembers((await getMembers(ws)).members);
  }
  async function onRemove(username: string) {
    await removeMember(ws, username);
    setMembers((await getMembers(ws)).members);
  }
  async function onOpen() {
    setOpen(true);
    setUsers((await listUsers()).users);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" onClick={onOpen} data-testid="member-manager">{t("members.manage")}</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader><DialogTitle>{t("members.title")}</DialogTitle></DialogHeader>
        <ul className="space-y-1">
          {members.map((m) => (
            <li key={m.user_id} className="flex items-center justify-between rounded border px-3 py-1.5 text-sm">
              <span>{m.username} <span className="text-xs text-muted-foreground">{t(`members.${m.role}`)}</span></span>
              <Button variant="ghost" size="sm" onClick={() => onRemove(m.username)}>{t("members.remove")}</Button>
            </li>
          ))}
        </ul>
        <div className="flex items-center gap-2">
          <Select value={picked} onValueChange={setPicked}>
            <SelectTrigger className="flex-1"><SelectValue placeholder={t("members.username")} /></SelectTrigger>
            <SelectContent>
              {users.filter((u) => !members.some((m) => m.user_id === u.id)).map((u) => (
                <SelectItem key={u.id} value={u.username}>{u.username}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button onClick={onAdd} disabled={!picked}>{t("members.add")}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
