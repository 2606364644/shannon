import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { getSsoConfig } from "@/api/client";
import { addSsoWhitelist, getSsoWhitelist, removeSsoWhitelist, type SsoWhitelistRow } from "@/api/ssoWhitelist";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

/** SSO 登录白名单管理（admin 页内，spec 2026-08-25 §8）：白名单内 OA nick 首登自动建户。 */
export function SsoWhitelistPanel() {
  const { t } = useTranslation();
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [rows, setRows] = useState<SsoWhitelistRow[] | null>(null);
  const [nick, setNick] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try {
      const cfg = await getSsoConfig();
      setEnabled(!!cfg.enabled);
      if (!cfg.enabled) return;
      setRows((await getSsoWhitelist()).whitelist);
    } catch {
      setEnabled(false);
    }
  }
  useEffect(() => { void refresh(); }, []);

  async function onAdd() {
    const v = nick.trim();
    if (!v || busy) return;
    setBusy(true);
    try {
      await addSsoWhitelist(v);
      setNick("");
      setRows((await getSsoWhitelist()).whitelist);
      toast.success(t("users.ssoWhitelist.added"));
    } catch {
      toast.error(t("users.ssoWhitelist.addFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function onRemove(n: string) {
    try {
      await removeSsoWhitelist(n);
      setRows((cur) => cur?.filter((r) => r.nick !== n) ?? cur);
      toast.success(t("users.ssoWhitelist.removed"));
    } catch {
      toast.error(t("users.ssoWhitelist.removeFailed"));
    }
  }

  if (enabled === null) return <Skeleton className="h-24 w-full" />;
  return (
    <Card className="space-y-3 p-4" data-testid="sso-whitelist-panel">
      <div>
        <h2 className="text-sm font-semibold">{t("users.ssoWhitelist.title")}</h2>
        <p className="text-xs text-muted-foreground">{t("users.ssoWhitelist.subtitle")}</p>
      </div>
      {enabled === false ? (
        <p className="text-xs text-muted-foreground">{t("users.ssoWhitelist.disabledHint")}</p>
      ) : (
        <>
          <div className="flex gap-2">
            <Input
              value={nick}
              onChange={(e) => setNick(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void onAdd(); }}
              placeholder={t("users.ssoWhitelist.placeholder")}
              data-testid="sso-whitelist-input"
            />
            <Button variant="cta" onClick={() => void onAdd()} disabled={busy || !nick.trim()}>
              {t("users.ssoWhitelist.add")}
            </Button>
          </div>
          {rows?.length === 0 && (
            <p className="text-xs text-muted-foreground">{t("users.ssoWhitelist.empty")}</p>
          )}
          {rows && rows.length > 0 && (
            <ul className="flex flex-wrap gap-2">
              {rows.map((r) => (
                <li key={r.nick} data-testid={`sso-whitelist-item-${r.nick}`}
                    className="flex items-center gap-1 rounded-md border border-border/60 bg-muted/40 px-2 py-1 text-xs">
                  <span className="font-mono">{r.nick}</span>
                  <button aria-label={`remove-${r.nick}`} className="text-muted-foreground hover:text-destructive"
                          onClick={() => void onRemove(r.nick)}>×</button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Card>
  );
}
