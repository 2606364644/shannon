import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PASSWORD_MIN_LEN } from "@/lib/password";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChanged?: () => void;
}

/**改密码弹窗：old/new/confirm 三字段，提交 POST /api/auth/change-password。
 * 成功后调 onChanged（让父组件刷新 user / 关弹窗）；失败按状态码提示。*/
export function ChangePasswordDialog({ open, onOpenChange, onChanged }: Props) {
  const { t } = useTranslation();
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function reset() {
    setOldPw(""); setNewPw(""); setConfirmPw(""); setError(null);
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (newPw.length < PASSWORD_MIN_LEN) {
      setError(t("auth.changePassword.tooShort"));
      return;
    }
    if (newPw !== confirmPw) {
      setError(t("auth.changePassword.mismatch"));
      return;
    }
    setBusy(true);
    try {
      const res = await fetch("/api/auth/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": readCsrf() },
        credentials: "include",
        body: JSON.stringify({ old_password: oldPw, new_password: newPw }),
      });
      if (!res.ok) {
        // 401 旧密码错；400 太短/新旧相同；403 CSRF
        if (res.status === 401) setError(t("auth.changePassword.wrongOld"));
        else if (res.status === 400) setError(t("auth.changePassword.tooShort"));
        else setError(t("auth.changePassword.failed"));
        setBusy(false);
        return;
      }
      reset();
      onChanged?.();
      onOpenChange(false);
    } catch {
      setError(t("auth.changePassword.failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) reset(); onOpenChange(o); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("auth.changePassword.title")}</DialogTitle>
        </DialogHeader>
        <form onSubmit={onSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="cp-old">{t("auth.changePassword.old")}</Label>
            <Input id="cp-old" type="password" value={oldPw} onChange={(e) => setOldPw(e.target.value)} autoComplete="current-password" required />
          </div>
          <div className="space-y-2">
            <Label htmlFor="cp-new">{t("auth.changePassword.new")}</Label>
            <Input id="cp-new" type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)} autoComplete="new-password" required />
          </div>
          <div className="space-y-2">
            <Label htmlFor="cp-confirm">{t("auth.changePassword.confirm")}</Label>
            <Input id="cp-confirm" type="password" value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)} autoComplete="new-password" required />
          </div>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              {t("auth.changePassword.skip")}
            </Button>
            <Button type="submit" disabled={busy}>{busy ? "…" : t("auth.changePassword.submit")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function readCsrf(): string {
  const m = document.cookie.match(/(?:^|; )sn-csrf=([^;]*)/);
  return m ? decodeURIComponent(m[1]) : "";
}
