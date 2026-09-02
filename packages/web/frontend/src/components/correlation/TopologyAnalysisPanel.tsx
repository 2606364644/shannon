import { useTranslation } from "react-i18next";
import { AlertTriangle, RefreshCw, StopCircle, Wand2, Wrench } from "lucide-react";
import { Button } from "@/components/ui/button";
import { fmtCost } from "@/utils/currency";
import type { CorrelationTopologyAnalysis } from "@/api/types";

interface Props {
  analysis: CorrelationTopologyAnalysis | null;
  starting: boolean;
  error: string | null;
  onStart: () => void;
  onRetry: () => void;
  onCancel: () => void;
  onManual: () => void;
}

export function CorrelationTopologyAnalysisPanel({
  analysis, starting, error, onStart, onRetry, onCancel, onManual,
}: Props) {
  const { t } = useTranslation();
  const active = analysis?.status === "queued" || analysis?.status === "running";
  const terminalFailure = analysis?.status === "failed" || analysis?.status === "cancelled"
    || analysis?.status === "interrupted";
  return (
    <section className="space-y-2 rounded-lg border border-border bg-card p-3" aria-label={t("scan.correlation.analysis.panel")}>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" onClick={active ? onCancel : terminalFailure || analysis?.status === "completed" ? onRetry : onStart}
          disabled={starting}>
          {active ? <StopCircle className="h-3.5 w-3.5" /> : terminalFailure ? <RefreshCw className="h-3.5 w-3.5" /> : <Wand2 className="h-3.5 w-3.5" />}
          {active ? t("scan.correlation.analysis.cancel") : terminalFailure || analysis?.status === "completed" ? t("scan.correlation.analysis.retry") : t("scan.correlation.analysis.start")}
        </Button>
        <Button type="button" variant="outline" size="sm" onClick={onManual}>
          <Wrench className="h-3.5 w-3.5" />
          {t("scan.correlation.analysis.manual")}
        </Button>
        {analysis && (
          <span className="rounded-full border border-border bg-card px-2 py-0.5 text-[11px] font-semibold">
            {t(`scan.correlation.analysis.status.${analysis.status}`)}
          </span>
        )}
        {analysis?.cache_hit && (
          <span className="rounded-full border border-border bg-card px-2 py-0.5 text-[11px] text-muted-foreground">
            {t("scan.correlation.analysis.cache")}
          </span>
        )}
        {typeof analysis?.progress === "number" && (
          <span className="text-[11px] text-muted-foreground">{analysis.progress}%</span>
        )}
      </div>
      {analysis?.usage && (
        <p className="text-xs text-muted-foreground">
          {t("scan.correlation.analysis.usage", {
            tokens: (analysis.usage.input_tokens ?? 0) + (analysis.usage.output_tokens ?? 0),
            cost: fmtCost(analysis.usage.cost_usd, analysis.usage.cost_currency),
          })}
        </p>
      )}
      {analysis?.status === "failed" || analysis?.status === "interrupted" ? (
        <p className="text-xs text-destructive">{analysis.error?.message ?? t("scan.correlation.analysis.failed")}</p>
      ) : null}
      {error && <p className="flex items-center gap-1 text-xs text-destructive"><AlertTriangle className="h-3.5 w-3.5" />{error}</p>}
    </section>
  );
}
