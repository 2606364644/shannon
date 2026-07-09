import { useTranslation } from "react-i18next";
import type { Severity } from "@/api/types";
import { SEVERITY_BG, type ReportStats } from "@/lib/report-stats";

const SEV_ORDER: Severity[] = ["Critical", "High", "Medium", "Low"];

/**
 * 威胁概览条（对齐设计稿 report-a `.threat`，配色换纯 severity）。
 * 三列：总数大数字 | severity 堆叠条 + 4 色图例 | 建议优先处置 Top3。
 */
export function ThreatOverview({ stats }: { stats: ReportStats }) {
  const { t } = useTranslation();
  const nonZero = SEV_ORDER.filter((s) => stats.severityDist[s] > 0);

  return (
    <section
      data-testid="threat-overview"
      aria-label={t("report.ariaLabel")}
      className="grid grid-cols-1 overflow-hidden rounded-md border border-border bg-card shadow-[var(--shadow-card)] md:grid-cols-[200px_1fr_280px]"
    >
      {/* 左：总数 */}
      <div className="flex flex-col justify-center border-border p-4 md:border-r">
        <div className="text-[52px] font-bold leading-none tracking-tight">
          {stats.total}
        </div>
        <div className="mt-1.5 font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
          {t("report.confirmedVulns", { count: stats.typeAggs.length })}
        </div>
        <div className="mt-2.5 text-xs">
          {t("report.publicReachable")} <b className="font-mono text-primary">{stats.publicCount}</b> · pre-auth{" "}
          <b className="font-mono text-primary">{stats.preAuthCount}</b>
        </div>
      </div>

      {/* 中：severity 堆叠条 + 图例 */}
      <div className="flex flex-col justify-center p-4">
        <div className="mb-2.5 flex items-baseline justify-between">
          <span className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
            {t("report.bySeverity")}
          </span>
          <span className="font-mono text-[11px] text-muted-foreground">{stats.total} total</span>
        </div>
        <div
          data-testid="threat-stack"
          className="flex h-9 overflow-hidden rounded-sm border border-border"
        >
          {nonZero.map((sev) => {
            const cnt = stats.severityDist[sev];
            return (
              <div
                key={sev}
                data-testid={`threat-seg-${sev}`}
                className={`${SEVERITY_BG[sev]} flex items-center justify-center font-mono text-xs font-semibold text-background`}
                style={{ flexGrow: cnt, flexBasis: 0 }}
                title={`${sev} ${cnt}`}
              >
                {cnt}
              </div>
            );
          })}
        </div>
        <div className="mt-3 grid grid-cols-4 gap-2">
          {SEV_ORDER.map((sev) => (
            <span
              key={sev}
              data-testid={`threat-legend-${sev}`}
              className="flex items-center gap-1.5 font-mono text-[10.5px] text-muted-foreground"
            >
              <i className={`inline-block size-2 rounded-sm ${SEVERITY_BG[sev]}`} aria-hidden="true" />
              {sev} <b className="font-medium text-foreground">{stats.severityDist[sev]}</b>
            </span>
          ))}
        </div>
      </div>

      {/* 右：优先处置 Top3 */}
      <div className="flex flex-col gap-1.5 border-border p-4 md:border-l">
        <div className="mb-0.5 font-mono text-[10.5px] uppercase tracking-wide text-red">
          {t("report.priorityTop")}
        </div>
        {stats.topRisks.map((r) => (
          <div
            key={r.vulnIds[0] ?? r.text}
            data-testid="threat-toprisk"
            className="flex items-center gap-2 text-[12.5px]"
          >
            {r.vulnIds.length > 0 && (
              <span className="shrink-0 whitespace-nowrap rounded-sm border border-red/30 bg-red/10 px-1.5 py-0.5 font-mono text-[11px] text-red">
                {r.vulnIds.join("/")}
              </span>
            )}
            <span className="truncate">{r.text}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
