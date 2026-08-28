import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { AlertTriangle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ErrorState";
import { useAuth } from "@/auth/AuthContext";
import { ApiError } from "@/api/client";
import { getPricing, putPricing, deletePricing, type PricingView } from "@/api/pricing";
import { PricingEditor } from "./PricingEditor";

/**
 * 全局定价卡（SettingsPage「定价」Section，spec 2026-08-28 §4.3 挂载点一）。
 * admin 可编辑（PUT /api/pricing = 完整生效表快照，保存即接管 profile env 层）；
 * 非 admin 只读。has_global_table 时提供「清除全局定价」（DELETE，回落 profile env / 内置）。
 * 生效时机：保存后 worker 下一次计费用新价，无需重启；已落盘历史成本不变。
 */
export function GlobalPricingCard() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [view, setView] = useState<PricingView | null>(null);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    try {
      setView(await getPricing());
      setError(false);
    } catch {
      setError(true);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  if (error) return <ErrorState message={t("settings.pricing.loadFailed")} onRetry={load} />;
  if (!view) return <Skeleton className="h-64 w-full" />;

  async function onSave(currency: string, models: Record<string, PricingView["builtin_defaults"][string]>) {
    try {
      await putPricing(currency, models);
      toast.success(t("settings.pricing.saved"));
      await load();
    } catch (e) {
      toast.error(e instanceof ApiError && e.status === 400
        ? String((e.body as { detail?: string })?.detail ?? t("settings.pricing.saveFailed"))
        : t("settings.pricing.saveFailed"));
    }
  }

  async function onClear() {
    if (!window.confirm(t("settings.pricing.clearGlobalConfirm"))) return;
    try {
      await deletePricing();
      toast.success(t("settings.pricing.cleared"));
      await load();
    } catch {
      toast.error(t("settings.pricing.clearFailed"));
    }
  }

  return (
    <Card>
      <CardHeader className="p-4 pb-3">
        <CardTitle className="font-semibold tracking-tight text-base">{t("settings.pricing.title")}</CardTitle>
        <p className="text-sm text-muted-foreground">{t("settings.pricing.desc")}</p>
      </CardHeader>
      <CardContent className="p-4 pt-0 space-y-3">
        {view.table_corrupt && (
          <div
            data-testid="pricing-corrupt-banner"
            className="flex items-center gap-2 rounded-md border border-amber/50 bg-amber/10 px-3 py-2 text-sm text-amber"
          >
            <AlertTriangle className="size-4 shrink-0" />
            {t("settings.pricing.corrupt")}
          </div>
        )}
        <PricingEditor
          scope="global"
          currency={view.currency}
          rows={view.models}
          builtinDefaults={view.builtin_defaults}
          canEdit={isAdmin}
          onSave={onSave}
          onClear={isAdmin && view.has_global_table ? onClear : undefined}
          hasOverride={view.has_global_table}
        />
        {isAdmin && !view.has_global_table && (
          <p className="text-xs text-muted-foreground">{t("settings.pricing.noGlobalNote")}</p>
        )}
      </CardContent>
    </Card>
  );
}
