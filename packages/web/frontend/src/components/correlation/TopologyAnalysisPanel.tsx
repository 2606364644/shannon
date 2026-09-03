import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, RefreshCw, StopCircle, Wand2, Wrench } from "lucide-react";
import { Button } from "@/components/ui/button";
import { fmtCost } from "@/utils/currency";
import { parseEventTs, fmtClock } from "@/utils/eventTs";
import type { CorrelationTopologyAnalysis, TopologyAuditLine } from "@/api/types";

interface Props {
  analysis: CorrelationTopologyAnalysis | null;
  starting: boolean;
  error: string | null;
  logLines: TopologyAuditLine[];
  logDropped?: number;
  onStart: () => void;
  onRetry: () => void;
  onCancel: () => void;
  onManual: () => void;
}

/** 过程日志行前缀/配色（与 tool-audit 事件类型一一对应）。 */
function lineStyle(line: TopologyAuditLine): { mark: string; cls: string } {
  switch (line.type) {
    case "tool_start": return { mark: "▶", cls: "text-foreground" };
    case "tool_end": return { mark: "✓", cls: "text-muted-foreground" };
    case "assistant_turn": return { mark: "●", cls: "text-sky-600 dark:text-sky-400" };
    case "error": return { mark: "✗", cls: "text-destructive" };
    default: return { mark: "·", cls: "text-muted-foreground" };
  }
}

function AuditLineRow({ line }: { line: TopologyAuditLine }) {
  const { mark, cls } = lineStyle(line);
  const epoch = parseEventTs(line.ts);
  const clock = Number.isNaN(epoch) ? "" : fmtClock(epoch);
  return (
    <div className="whitespace-nowrap truncate">
      {clock && <span className="text-muted-foreground/70">{clock} </span>}
      <span className={cls}>{mark} </span>
      {line.tool && <span className="font-semibold">{line.tool} </span>}
      <span className={cls}>{line.summary}</span>
    </div>
  );
}

/** 过程日志尾窗：近底自动跟随（用户上翻查看历史时不拽回），新行到达才滚。 */
function AuditTrail({ lines, dropped }: { lines: TopologyAuditLine[]; dropped?: number }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const lastNo = lines.length ? lines[lines.length - 1].no : -1;
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [lastNo]);
  return (
    <div ref={ref}
      className="h-40 overflow-y-auto rounded-md border border-border bg-muted/40 p-2 font-mono text-[11px] leading-5">
      {dropped ? (
        <div className="text-muted-foreground/60">… {dropped} ↑</div>
      ) : null}
      {lines.map((line) => <AuditLineRow key={line.no} line={line} />)}
      {lines.length === 0 && !dropped ? (
        <div className="text-muted-foreground/60">…</div>
      ) : null}
    </div>
  );
}

export function CorrelationTopologyAnalysisPanel({
  analysis, starting, error, logLines, logDropped, onStart, onRetry, onCancel, onManual,
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
      {analysis && (logLines.length > 0 || active) && (
        <AuditTrail lines={logLines} dropped={logDropped} />
      )}
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
