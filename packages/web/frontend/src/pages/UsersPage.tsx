import { Fragment, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { ChevronRight, ChevronDown } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { ErrorState } from "@/components/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { CreateUserDialog } from "@/components/CreateUserDialog";
import { ResetPasswordDialog } from "@/components/ResetPasswordDialog";
import { ConfirmDeleteUserDialog } from "@/components/ConfirmDeleteUserDialog";
import { UserWorkspacesPanel } from "@/components/UserWorkspacesPanel";
import { SsoWhitelistPanel } from "@/components/SsoWhitelistPanel";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
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
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

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
      <PageHeader
        title={t("users.title")}
        subtitle={t("users.subtitle")}
        action={<Button variant="cta" onClick={() => setCreateOpen(true)}>{t("users.create")}</Button>}
      />
      <CreateUserDialog open={createOpen} onOpenChange={setCreateOpen} onCreated={refresh} />
      {loading && <Skeleton className="h-40 w-full" />}
      {error && <ErrorState message={error} onRetry={refresh} />}
      {!loading && !error && (
        <Card className="overflow-hidden p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("users.username")}</TableHead>
                <TableHead>{t("users.role")}</TableHead>
                <TableHead>{t("users.mustChange")}</TableHead>
                <TableHead>{t("users.createdAt")}</TableHead>
                <TableHead>{t("users.actions")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {users.map((u) => (
                <Fragment key={u.id}>
                  <TableRow data-testid={`user-row-${u.username}`}>
                    <TableCell className="font-mono">
                      <span className="flex items-center gap-1.5">
                        <button
                          className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                          aria-label="toggle-workspaces"
                          onClick={() => setExpanded((s) => { const n = new Set(s); n.has(u.id) ? n.delete(u.id) : n.add(u.id); return n; })}
                        >{expanded.has(u.id) ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}</button>
                        <span>{u.username}</span>
                      </span>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className={u.role === "admin" ? "border-orange/40 text-orange" : "text-muted-foreground"}>
                        {t(`users.role${u.role === "admin" ? "Admin" : "User"}`)}
                      </Badge>
                    </TableCell>
                    <TableCell>{u.must_change_password && <Badge variant="outline" className="border-amber/50 text-amber">{t("users.mustChange")}</Badge>}</TableCell>
                    <TableCell className="text-muted-foreground">{u.created_at.slice(0, 10)}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap items-center gap-2">
                        <Select defaultValue={u.role} disabled={u.id === me?.id} onValueChange={async (v) => {
                          try { await updateRole(u.id, v as "admin" | "user"); toast.success(t("users.roleChanged")); void refresh(); }
                          catch { toast.error(t("users.roleChangeFailed")); }
                        }}>
                          <SelectTrigger className="w-28"><SelectValue /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value="user">{t("users.roleUser")}</SelectItem>
                            <SelectItem value="admin">{t("users.roleAdmin")}</SelectItem>
                          </SelectContent>
                        </Select>
                        <Button variant="ghost" size="sm" disabled={u.id === me?.id}
                                onClick={() => { setResetTarget(u); setResetOpen(true); }}>{t("users.resetPassword")}</Button>
                        <Button variant="ghost" size="sm" className="text-destructive hover:bg-destructive/10" disabled={u.id === me?.id}
                                onClick={() => { setDelTarget(u); setDelOpen(true); }}>{t("users.delete")}</Button>
                      </div>
                    </TableCell>
                  </TableRow>
                  {expanded.has(u.id) && (
                    <TableRow className="bg-muted/30 hover:bg-muted/30"><TableCell colSpan={5} className="py-2"><UserWorkspacesPanel user={u} /></TableCell></TableRow>
                  )}
                </Fragment>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
      <SsoWhitelistPanel />
      {resetTarget && <ResetPasswordDialog userId={resetTarget.id} open={resetOpen} onOpenChange={setResetOpen} />}
      {delTarget && <ConfirmDeleteUserDialog user={delTarget} open={delOpen} onOpenChange={setDelOpen} onDeleted={refresh} />}
    </div>
  );
}
