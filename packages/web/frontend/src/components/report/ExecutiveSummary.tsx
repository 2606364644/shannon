import type { MouseEvent } from "react";
import { useTranslation } from "react-i18next";
import type { ReportExecutiveSummary } from "@/api/types";
import { RichText } from "./RichText";

/**
 * 执行摘要（spec 2026-08-26 §7.2）：吃 report_data.executive_summary（④ agent 产物），
 * 纯渲染——叙事 / 风险等级 / top_risks 锚点（链接到同页 VulnerabilityCard，卡 id=vuln_id）
 * / 修复优先级。对齐 md 路径 exec-summary-hero 的视觉（红左规 + 卡面），但数据来自
 * 结构化字段，不再从编号列表 prose 里猜 ID（extractVulnIds 已删职责）。
 */
export function ExecutiveSummary({ summary }: { summary: ReportExecutiveSummary }) {
  const { t } = useTranslation();
  const scrollTo = (e: MouseEvent<HTMLAnchorElement>, id: string) => {
    e.preventDefault();
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  return (
    <section
      data-testid="exec-summary"
      className="space-y-3 rounded-md border border-border border-l-2 border-l-red/60 bg-card p-4 shadow-[var(--shadow-card)]"
    >
      {/* 满宽报告页（2026-08-26 放宽）：justify-start 让风险等级徽章紧跟标题，
          避免 justify-between 在宽屏把徽章推到远端显得松散。 */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-base font-semibold tracking-tight">{t("report.execSummary")}</span>
        {summary.risk_level && (
          <span
            data-testid="exec-risk-level"
            className="rounded-full bg-red/15 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-red"
          >
            {t("report.riskLevel")}: {summary.risk_level}
          </span>
        )}
      </div>
      {summary.narrative && <RichText text={summary.narrative} />}
      {summary.top_risks.length > 0 && (
        <div>
          <div className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            {t("report.topRisks")}
          </div>
          <ol className="list-decimal max-w-3xl space-y-1 pl-6 text-sm">
            {summary.top_risks.map((r, i) => (
              <li key={i} className="min-w-0 break-words">
                <a
                  href={`#${r.vuln_id}`}
                  onClick={(e) => scrollTo(e, r.vuln_id)}
                  className="kv-vuln-id font-mono text-[13px] text-primary"
                  data-testid="top-risk-link"
                >
                  {r.vuln_id}
                </a>
                {r.priority && (
                  <span className="ml-1.5 rounded-full border border-red/40 px-1.5 py-0.5 font-mono text-[10px] text-red">
                    {r.priority}
                  </span>
                )}
                {r.reason && <span className="ml-1.5 text-foreground/80">{r.reason}</span>}
              </li>
            ))}
          </ol>
        </div>
      )}
      {summary.remediation_order && (
        <div>
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            {t("report.remediationOrder")}
          </div>
          <RichText text={summary.remediation_order} />
        </div>
      )}
    </section>
  );
}
