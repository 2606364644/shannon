import { useTranslation } from "react-i18next";
import type { ReportStatsData } from "@/api/types";
import { SEV_CAP, SEV_PILL, SEV_DOT } from "@/lib/severity-visual";

/** severity 展示序（小写=report_data.json 键）；视觉映射单源 lib/severity-visual
 *  （spec 2026-08-27 §2.4：hue 药丸 + 填充比例 dot）。 */
const SEV_ORDER = ["critical", "high", "medium", "low"] as const;

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
      {types.length > 0 && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
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
              <span className={`sev-dot ${SEV_DOT[cap]}`} aria-hidden="true" />
              {cap} <b className="font-medium">{stats.by_severity?.[sev] ?? 0}</b>
            </span>
          );
        })}
      </div>
    </section>
  );
}
