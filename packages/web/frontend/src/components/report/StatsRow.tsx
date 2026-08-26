import { useTranslation } from "react-i18next";
import type { ReportStatsData } from "@/api/types";

/** severity 展示序（小写=report_data.json 键），配色对齐 md 路径 ThreatOverview 暖色通道。 */
const SEV_ORDER = ["critical", "high", "medium", "low"] as const;
const SEV_CAP: Record<string, string> = {
  critical: "Critical", high: "High", medium: "Medium", low: "Low",
};
const SEV_PILL: Record<string, string> = {
  Critical: "bg-red/15 text-red",
  High: "bg-orange/15 text-orange",
  Medium: "bg-yellow/15 text-yellow",
  Low: "bg-muted text-muted-foreground",
};
const SEV_DOT: Record<string, string> = {
  Critical: "bg-red", High: "bg-orange", Medium: "bg-yellow", Low: "bg-muted-foreground",
};

/**
 * 统计行（spec 2026-08-26 §7.2）：吃 report_data.stats——确定性聚合由组装器
 * （core report_data_builder）算好，前端纯展示。零计数类型照显（数据自带
 * by_type 条目，前端不做「被测类型补全」推断——md 路径 report-stats 的零计数
 * 补全/DISPLAY_TO_PREFIX 反查等在此路径不存在）。by_severity 缺键按 0 展示。
 */
export function StatsRow({ stats }: { stats: ReportStatsData }) {
  const { t } = useTranslation();
  const types = Object.entries(stats.by_type ?? {});
  return (
    <section data-testid="stats-row" className="space-y-3" aria-label={t("report.byType")}>
      {/* auto-fit：类型卡数量可变（by_type 条目），满宽报告页下按 ≥220px 自适应
          列数，不再固定 5 列在类型少时留空位、多时挤（2026-08-26 放宽配套）。 */}
      {types.length > 0 && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-[repeat(auto-fit,minmax(220px,1fr))]">
          {types.map(([key, ts]) => (
            <article
              key={key}
              data-testid={`stat-type-${key}`}
              className="relative overflow-hidden rounded-md border border-border bg-card p-3.5 shadow-[var(--shadow-card)]"
            >
              <div className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
                {key}
              </div>
              <div className="mt-1.5 text-[34px] font-bold leading-none tracking-tight">
                {ts.count}
              </div>
              <div className="mb-2 mt-1 font-mono text-[10.5px]">
                {ts.count === 0 || !ts.severity_range ? (
                  <span className="text-muted-foreground">N/A</span>
                ) : (
                  <span>{ts.severity_range}</span>
                )}
              </div>
              {ts.key_findings && (
                <p className="border-t border-dashed border-border pt-2 text-[11.5px] leading-snug text-foreground/85">
                  {ts.key_findings}
                </p>
              )}
            </article>
          ))}
        </div>
      )}
      <div
        data-testid="stat-severity"
        className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-md border border-border bg-card p-3 shadow-[var(--shadow-card)]"
      >
        <span className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
          {t("report.bySeverity")}
        </span>
        {SEV_ORDER.map((sev) => {
          const cap = SEV_CAP[sev];
          return (
            <span
              key={sev}
              data-testid={`stat-sev-${sev}`}
              className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 font-mono text-[10.5px] ${SEV_PILL[cap]}`}
            >
              <span className={`size-1.5 rounded-full ${SEV_DOT[cap]}`} aria-hidden="true" />
              {cap} <b className="font-medium">{stats.by_severity?.[sev] ?? 0}</b>
            </span>
          );
        })}
      </div>
    </section>
  );
}
