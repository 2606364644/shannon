import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { resetPassword } from "@/api/users";
import { PASSWORD_MIN_LEN } from "@/lib/password";
import { apiErrorMessage } from "@/lib/apiError";

export function ResetPasswordDialog({ userId, open, onOpenChange }: {
  userId: number; open: boolean; onOpenChange: (o: boolean) => void;
}) {
  const { t } = useTranslation();
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (pw.length < PASSWORD_MIN_LEN) {
      toast.error(t("users.passwordMinLength"));
      return;
    }
    setBusy(true);
    try {
      await resetPassword(userId, pw);
      toast.success(t("users.passwordReset"));
      setPw("");
      onOpenChange(false);
    } catch (e) {
      toast.error(apiErrorMessage(e, t("users.passwordResetFailed")));
    } finally { setBusy(false); }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader><DialogTitle>{t("users.resetPassword")}</DialogTitle></DialogHeader>
        <form onSubmit={onSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="rp-pw">{t("users.newPassword")}</Label>
            <Input id="rp-pw" type="password" value={pw} onChange={(e) => setPw(e.target.value)} required />
            <p className="text-xs text-muted-foreground">{t("users.passwordHint")}</p>
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>{t("users.cancel")}</Button>
            <Button type="submit" disabled={busy}>{busy ? "…" : t("users.resetPassword")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
