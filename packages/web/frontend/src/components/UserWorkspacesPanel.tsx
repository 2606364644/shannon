import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { getUserWorkspaces, type UserRow, type UserWorkspace } from "@/api/users";
import { addMember, removeMember } from "@/api/members";
import { apiPatch, apiGet } from "@/api/client";

type WsInfo = { name: string };
type MemberOf = Record<string, "manager" | "member">;

export function UserWorkspacesPanel({ user }: { user: UserRow }) {
  const { t } = useTranslation();
  const [memberOf, setMemberOf] = useState<MemberOf>({});
  const [allWs, setAllWs] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    try {
      const [own, wsList] = await Promise.all([
        getUserWorkspaces(user.id),
        apiGet<WsInfo[]>("/workspaces"),
      ]);
      const map: MemberOf = {};
      own.workspaces.forEach((w: UserWorkspace) => { map[w.workspace] = w.role; });
      setMemberOf(map);
      setAllWs(wsList.map((w) => w.name));
    } catch {
      // 面内错误态：静默，loading 结束后展示空/既有
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { void load(); }, [user.id]);

  async function onAdd(ws: string) {
    try {
      await addMember(ws, user.username, "member");
      toast.success(t("users.members.saved"));
      void load();
    } catch {
      toast.error(t("users.members.saveFailed"));
      void load();
    }
  }
  async function onRoleChange(ws: string, role: "manager" | "member") {
    try {
      await apiPatch(`/workspaces/${encodeURIComponent(ws)}/members/${encodeURIComponent(user.username)}`, { role });
      toast.success(t("users.members.saved"));
      void load();
    } catch {
      toast.error(t("users.members.saveFailed"));
      void load();
    }
  }
  async function onRemove(ws: string) {
    try {
      await removeMember(ws, user.username);
      toast.success(t("users.members.saved"));
      void load();
    } catch {
      toast.error(t("users.members.saveFailed"));
      void load();
    }
  }

  if (loading) return <Skeleton className="h-20 w-full" />;
  return (
    <div className="rounded border p-3 space-y-2" data-testid={`wsp-${user.username}`}>
      <p className="text-sm font-medium">{t("users.members.title")}</p>
      {allWs.length === 0 && <p className="text-sm text-muted-foreground">{t("users.members.empty")}</p>}
      {allWs.map((ws) => {
        const role = memberOf[ws];
        return (
          <div key={ws} className="flex items-center justify-between text-sm">
            <span className="font-mono">{ws}</span>
            {role ? (
              <>
                <Select value={role} onValueChange={(v) => onRoleChange(ws, v as "manager" | "member")}>
                  <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="member">{t("users.members.wsRole")} member</SelectItem>
                    <SelectItem value="manager">manager</SelectItem>
                  </SelectContent>
                </Select>
                <Button variant="ghost" size="sm" onClick={() => onRemove(ws)}>{t("users.members.remove")}</Button>
              </>
            ) : (
              <Button variant="outline" size="sm" data-testid={`add-${ws}`} onClick={() => onAdd(ws)}>{t("users.members.add")}</Button>
            )}
          </div>
        );
      })}
    </div>
  );
}
