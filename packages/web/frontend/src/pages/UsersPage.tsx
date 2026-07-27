import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { PageHeader } from "@/components/PageHeader";
import { ErrorState } from "@/components/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { CreateUserDialog } from "@/components/CreateUserDialog";
import { ResetPasswordDialog } from "@/components/ResetPasswordDialog";
import { ConfirmDeleteUserDialog } from "@/components/ConfirmDeleteUserDialog";
import { useAuth } from "@/auth/AuthContext";
import { listUsers, updateRole, type UserRow } from "@/api/users";

export function UsersPage() {
  const { t } = useTranslation();
  const [users, setUsers] = useState<UserRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const { user: me } = useAuth();
  const [resetTarget, setResetTarget] = useState<UserRow | null>(null);
  const [resetOpen, setResetOpen] = useState(false);
  const [delTarget, setDelTarget] = useState<UserRow | null>(null);
  const [delOpen, setDelOpen] = useState(false);

  async function refresh() {
    setLoading(true); setError(null);
    try {
      const r = await listUsers();
      setUsers(r.users);
    } catch {
      setError(t("users.loadFailed"));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { void refresh(); }, []);

  return (
    <div className="space-y-4">
      <PageHeader title={t("users.title")} />
      <p className="text-sm text-muted-foreground">{t("users.subtitle")}</p>
      <div className="flex justify-end">
        <Button onClick={() => setCreateOpen(true)}>{t("users.create")}</Button>
      </div>
      <CreateUserDialog open={createOpen} onOpenChange={setCreateOpen} onCreated={refresh} />
      {loading && <Skeleton className="h-40 w-full" />}
      {error && <ErrorState message={error} onRetry={refresh} />}
      {!loading && !error && (
        <table className="w-full text-sm">
          <thead className="text-left text-muted-foreground">
            <tr>
              <th className="py-2">{t("users.username")}</th>
              <th className="py-2">{t("users.role")}</th>
              <th className="py-2">{t("users.mustChange")}</th>
              <th className="py-2">{t("users.createdAt")}</th>
              <th className="py-2">{t("users.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-t" data-testid={`user-row-${u.username}`}>
                <td className="py-2 font-mono">{u.username}</td>
                <td className="py-2">{t(`users.role${u.role === "admin" ? "Admin" : "User"}`)}</td>
                <td className="py-2">{u.must_change_password && <Badge variant="outline" className="border-amber/50 text-amber">{t("users.mustChange")}</Badge>}</td>
                <td className="py-2 text-muted-foreground">{u.created_at.slice(0, 10)}</td>
                <td className="py-2 space-x-2">
                  <Select defaultValue={u.role} disabled={u.id === me?.id} onValueChange={async (v) => {
                    try { await updateRole(u.id, v as "admin" | "user"); toast.success(t("users.roleChanged")); void refresh(); }
                    catch { toast.error(t("users.roleChangeFailed")); }
                  }}>
                    <SelectTrigger className="w-28"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="user">{t("users.roleUser")}</SelectItem>
                      <SelectItem value="admin">{t("users.roleAdmin")}</SelectItem>
                    </SelectContent>
                  </Select>{" "}
                  <Button variant="outline" size="sm" disabled={u.id === me?.id}
                          onClick={() => { setResetTarget(u); setResetOpen(true); }}>{t("users.resetPassword")}</Button>{" "}
                  <Button variant="outline" size="sm" disabled={u.id === me?.id}
                          onClick={() => { setDelTarget(u); setDelOpen(true); }}>{t("users.delete")}</Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {resetTarget && <ResetPasswordDialog userId={resetTarget.id} open={resetOpen} onOpenChange={setResetOpen} />}
      {delTarget && <ConfirmDeleteUserDialog user={delTarget} open={delOpen} onOpenChange={setDelOpen} onDeleted={refresh} />}
    </div>
  );
}
