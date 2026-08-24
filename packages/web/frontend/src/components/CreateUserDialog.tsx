import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { createUser } from "@/api/users";
import { PASSWORD_MIN_LEN } from "@/lib/password";
import { apiErrorMessage } from "@/lib/apiError";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}

export function CreateUserDialog({ open, onOpenChange, onCreated }: Props) {
  const { t } = useTranslation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"admin" | "user">("user");
  const [busy, setBusy] = useState(false);

  function reset() { setUsername(""); setPassword(""); setRole("user"); }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    // 留空 -> 后端落默认密码（DEFAULT_NEW_USER_PASSWORD）；手填才校验长度。
    if (password && password.length < PASSWORD_MIN_LEN) {
      toast.error(t("users.passwordMinLength"));
      return;
    }
    setBusy(true);
    try {
      await createUser({ username, password, role });
      toast.success(t("users.created"));
      reset();
      onCreated();
      onOpenChange(false);
    } catch (e) {
      // 透传后端真实原因（409 重名 "username exists" 等）；非 ApiError 或无 detail 退回笼统提示
      toast.error(apiErrorMessage(e, t("users.createFailed")));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) reset(); onOpenChange(o); }}>
      <DialogContent>
        <DialogHeader><DialogTitle>{t("users.create")}</DialogTitle></DialogHeader>
        <form onSubmit={onSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="cu-user">{t("users.username")}</Label>
            <Input id="cu-user" value={username} onChange={(e) => setUsername(e.target.value)} required />
          </div>
          <div className="space-y-2">
            <Label htmlFor="cu-pw">{t("users.password")}</Label>
            <Input id="cu-pw" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
            <p className="text-xs text-muted-foreground">{t("users.passwordHint")}</p>
          </div>
          <div className="space-y-2">
            <Label>{t("users.role")}</Label>
            <Select value={role} onValueChange={(v) => setRole(v as "admin" | "user")}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="user">{t("users.roleUser")}</SelectItem>
                <SelectItem value="admin">{t("users.roleAdmin")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>{t("users.cancel")}</Button>
            <Button type="submit" disabled={busy}>{busy ? "…" : t("users.create")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
