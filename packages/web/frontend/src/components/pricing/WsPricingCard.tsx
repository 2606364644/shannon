import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { AlertTriangle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ErrorState";
import { useAuth } from "@/auth/AuthContext";
import { getMembers } from "@/api/members";
import type { Member } from "@/api/members";
import { ApiError } from "@/api/client";
import { getWsPricing, putWsPricing, deleteWsPricing, type WsPricingView, type Prices } from "@/api/pricing";
import { PricingEditor } from "./PricingEditor";

/**
 * 工作区定价卡（WsSettingsTab 下方，spec 2026-08-28 §4.3 挂载点二）。
 * 继承态：只读展示当前生效表（来源徽章可见 env 手写 / 全局各层）；
 * 覆盖态：workspace scope 编辑器（覆盖文件 pricing.override.json 为 SSOT）+ 清除覆盖恢复继承。
 * canEdit = admin | manager（workspace 级角色，members API——WsSettingsTab 同款先例）。
 */
export default function WsPricingCard() {
  const { workspace: ws = "" } = useParams<{ workspace: string }>();
  const { t } = useTranslation();
  const { user } = useAuth();
  const [view, setView] = useState<WsPricingView | null>(null);
  const [error, setError] = useState(false);
  const [members, setMembers] = useState<Member[]>([]);
  const [editing, setEditing] = useState(false);

  const load = useCallback(async () => {
    try {
      setView(await getWsPricing(ws));
      setError(false);
    } catch {
      setError(true);
    }
  }, [ws]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { getMembers(ws).then((r) => setMembers(r.members)).catch(() => {}); }, [ws]);

  // workspace 级角色来自 members API（全局 user.role 只有 admin/user）
  const myRole = user?.role === "admin" ? "admin" : members.find((m) => m.user_id === user?.id)?.role;
  const canEdit = myRole === "admin" || myRole === "manager";

  if (error) return <ErrorState message={t("wsConfig.pricing.loadFailed")} onRetry={load} />;
  if (!view) return <Skeleton className="h-64 w-full" />;

  async function onSave(currency: string, models: Record<string, Prices>) {
    try {
      await putWsPricing(ws, currency, models);
      toast.success(t("wsConfig.pricing.saved"));
      setEditing(false);
      await load();
    } catch (e) {
      toast.error(e instanceof ApiError && e.status === 400
        ? String((e.body as { detail?: string })?.detail ?? t("wsConfig.pricing.saveFailed"))
        : t("wsConfig.pricing.saveFailed"));
    }
  }

  async function onClear() {
    if (!window.confirm(t("wsConfig.pricing.clearOverrideConfirm"))) return;
    try {
      await deleteWsPricing(ws);
      toast.success(t("wsConfig.pricing.cleared"));
      setEditing(false);
      await load();
    } catch {
      toast.error(t("wsConfig.pricing.clearFailed"));
    }
  }

  const overridden = view.override_exists;
  const showEditor = overridden || editing;

  return (
    <Card>
      <CardHeader className="p-4 pb-3">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="font-semibold tracking-tight text-base">{t("wsConfig.pricing.title")}</CardTitle>
          {overridden && (
            <Badge data-testid="ws-pricing-overridden" variant="outline" className="border-primary/60 text-primary">
              {t("wsConfig.pricing.overriddenBadge")}
            </Badge>
          )}
        </div>
        <p className="text-sm text-muted-foreground">{t("wsConfig.pricing.subtitle")}</p>
      </CardHeader>
      <CardContent className="p-4 pt-0 space-y-3">
        {view.table_corrupt && (
          <div
            data-testid="pricing-corrupt-banner"
            className="flex items-center gap-2 rounded-md border border-amber/50 bg-amber/10 px-3 py-2 text-sm text-amber"
          >
            <AlertTriangle className="size-4 shrink-0" />
            {t("wsConfig.pricing.corrupt")}
          </div>
        )}

        {!showEditor && (
          <p className="text-xs text-muted-foreground" data-testid="ws-pricing-inherit-note">
            {t("wsConfig.pricing.inheritNote")}
          </p>
        )}

        <PricingEditor
          scope="workspace"
          currency={view.currency}
          rows={view.models}
          builtinDefaults={view.builtin_defaults}
          canEdit={canEdit && showEditor}
          onSave={onSave}
          onClear={canEdit && overridden ? onClear : undefined}
          hasOverride={overridden}
        />

        <div className="flex flex-wrap items-center gap-2">
          {canEdit && !overridden && !editing && (
            <Button
              type="button" variant="outline" size="sm"
              data-testid="ws-pricing-override-btn"
              onClick={() => setEditing(true)}
            >
              {t("wsConfig.pricing.overrideBtn")}
            </Button>
          )}
          {canEdit && editing && !overridden && (
            <Button
              type="button" variant="ghost" size="sm"
              className="text-muted-foreground"
              onClick={() => setEditing(false)}
            >
              {t("wsConfig.pricing.cancelOverride")}
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
