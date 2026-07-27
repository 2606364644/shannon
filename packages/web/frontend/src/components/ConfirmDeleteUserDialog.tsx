import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { deleteUser, type UserRow } from "@/api/users";

export function ConfirmDeleteUserDialog({ user, open, onOpenChange, onDeleted }: {
  user: UserRow; open: boolean; onOpenChange: (o: boolean) => void; onDeleted: () => void;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  async function onConfirm() {
    setBusy(true);
    try {
      await deleteUser(user.id);
      toast.success(t("users.deleted"));
      onDeleted();
      onOpenChange(false);
    } catch {
      toast.error(t("users.deleteFailed"));
    } finally { setBusy(false); }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader><DialogTitle>{t("users.delete")}</DialogTitle></DialogHeader>
        <p className="text-sm text-destructive">{t("users.deleteConfirm", { name: user.username })}</p>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{t("users.cancel")}</Button>
          <Button variant="destructive" onClick={onConfirm} disabled={busy}>{busy ? "…" : t("users.deleteConfirmBtn")}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
