import { useTranslation } from "react-i18next";
import type { ReportData } from "@/api/types";
import { ExecutiveSummary } from "./ExecutiveSummary";
import { StatsRow } from "./StatsRow";
import { VulnerabilityCard } from "./VulnerabilityCard";
import { RichText } from "./RichText";
import { ReportToc, REPORT_CHAINS_ID } from "./ReportToc";

/**
 * 结构化报告视图（spec 2026-08-26 §7.2，T6）：吃 GET .../report-data 的
 * report_data.json——三轨（whitebox/blackbox/combined）统一 SSOT。纯渲染组件族
 * 的根：ExecutiveSummary（叙事 + top_risks 锚点）→ StatsRow（确定性聚合）→
 * VulnerabilityCard 列表 → 攻击链 + QA 标记。不做任何解析/推断/归并——那些在
 * 生成层完成；旧 scan 无 report_data.json 时由 ReportTab 回退 md 渲染路径。
 * 2026-08-26 加目录（ReportToc）：条目 ≥2 起两栏（对齐 md 路径 TOC 门槛），lg 以下
 * 单列不占移动端宽度；跳转经 focusAnchor 精准落点（量 sticky 遮蔽带，卡头标题完整
 * 露出 + coral 闪烁）。
 */
export function ReportView({ data }: { data: ReportData }) {
  const { t } = useTranslation();
  const failedChecks = (data.qa?.checks ?? []).filter((c) => c.failed_ids.length > 0);
  const tocEntryCount =
    (data.executive_summary ? 1 : 0) +
    data.vulnerabilities.length +
    (data.attack_chains.length > 0 ? 1 : 0);
  const twoCol = tocEntryCount >= 2;

  const body = (
    <div className="min-w-0 space-y-5">
      {data.executive_summary && <ExecutiveSummary summary={data.executive_summary} />}
      {data.stats && <StatsRow stats={data.stats} />}
      {data.qa && !data.qa.passed && (
        <div
          data-testid="report-qa-banner"
          className="rounded-md border border-yellow/40 bg-yellow/10 p-3 text-sm text-yellow"
        >
          {t("report.qaFailed")}
          {failedChecks.length > 0 && ` — ${failedChecks.map((c) => c.check).join("；")}`}
        </div>
      )}
      <div className="space-y-4">
        {data.vulnerabilities.map((v) => (
          <VulnerabilityCard key={v.id} v={v} />
        ))}
      </div>
      {data.attack_chains.length > 0 && (
        <section
          id={REPORT_CHAINS_ID}
          data-testid="report-chains"
          className="space-y-3 rounded-md border border-border bg-card p-4 shadow-[var(--shadow-card)]"
        >
          <h2 className="text-base font-semibold tracking-tight">{t("report.attackChains")}</h2>
          {data.attack_chains.map((c) => (
            <div key={c.id} data-testid="report-chain" className="space-y-1">
              <code className="font-mono text-[12px] text-primary">{c.id}</code>
              {c.narrative && <RichText text={c.narrative} />}
            </div>
          ))}
        </section>
      )}
    </div>
  );

  if (!twoCol) return <div data-testid="report-view">{body}</div>;
  return (
    <div data-testid="report-view" className="lg:grid lg:grid-cols-[210px_minmax(0,1fr)] lg:gap-6">
      {/* 目录左栏：lg 起两栏（移动端不占宽）；sticky top-44 ≈ TopBar(48px) + 进度 tabs
          sticky 块典型高度，自身内滚。跳转落点由 focusAnchor 运行时量取，不依赖此值。 */}
      <aside className="sticky top-44 hidden max-h-[calc(100vh-12rem)] self-start overflow-y-auto pr-1 lg:block">
        <ReportToc data={data} />
      </aside>
      {body}
    </div>
  );
}
