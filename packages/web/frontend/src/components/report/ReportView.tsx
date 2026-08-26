import { useCallback, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ListCollapse, ListTree } from "lucide-react";
import type { ReportData } from "@/api/types";
import { focusAnchor } from "@/utils/focusAnchor";
import { ExecutiveSummary } from "./ExecutiveSummary";
import { StatsRow } from "./StatsRow";
import { QuickReferenceTable } from "./QuickReferenceTable";
import { VulnerabilityCard } from "./VulnerabilityCard";
import { RichText } from "./RichText";
import { ReportToc, REPORT_CHAINS_ID } from "./ReportToc";

/**
 * 结构化报告视图（spec 2026-08-26 §7.2，T6）：吃 GET .../report-data 的
 * report_data.json——三轨（whitebox/blackbox/combined）统一 SSOT。纯渲染组件族
 * 的根：ExecutiveSummary（叙事 + top_risks 锚点）→ StatsRow（类型汇总）→
 * QuickReferenceTable（速查表，单源化 §5/§7）→ VulnerabilityCard 列表 → 攻击链
 * + QA 标记。不做任何解析/推断/归并——那些在生成层完成；旧 scan 无 report_data.json
 * 时由 ReportTab 回退 md 渲染路径。
 * 目录（ReportToc）：条目 ≥2 起两栏，跳转经 focusAnchor 精准落点。
 * 整卡折叠（单源化 §7）：ReportView 持 collapsed 集中 state（受控下传卡头
 * button）+ 批量收起/展开；目录/速查表/top_risks/qa 徽章定位统一走 locateVuln
 * ——目标卡折叠时先展开（等重渲染后）再定位，未折叠同步定位。
 */
export function ReportView({ data }: { data: ReportData }) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const failedChecks = (data.qa?.checks ?? []).filter((c) => c.failed_ids.length > 0);
  const tocEntryCount =
    (data.executive_summary ? 1 : 0) +
    data.vulnerabilities.length +
    (data.attack_chains.length > 0 ? 1 : 0);
  const twoCol = tocEntryCount >= 2;

  const vulnIds = useMemo(() => data.vulnerabilities.map((v) => v.id), [data.vulnerabilities]);
  const collapseAll = () => setCollapsed(new Set(vulnIds));
  const expandAll = () => setCollapsed(new Set());

  /** 定位漏洞卡（折叠联动）：折叠 → 先展开，等 React 重渲染（卡身挂载）后再
   *  focusAnchor（否则量到的 rect 是折叠卡头位置）；未折叠同步定位（现有目录
   * 跳转测试的同步断言路径）。 */
  const locateVuln = useCallback(
    (id: string) => {
      let wasCollapsed = false;
      setCollapsed((prev) => {
        if (!prev.has(id)) return prev;
        wasCollapsed = true;
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      if (wasCollapsed) setTimeout(() => focusAnchor(id), 0);
      else focusAnchor(id);
    },
    [],
  );

  const body = (
    <div className="min-w-0 space-y-5">
      {data.executive_summary && (
        <ExecutiveSummary summary={data.executive_summary} onLocateRisk={locateVuln} />
      )}
      {data.stats && <StatsRow stats={data.stats} />}
      {data.quick_reference && data.quick_reference.length > 0 && (
        <QuickReferenceTable rows={data.quick_reference} onLocate={locateVuln} />
      )}
      {data.qa && !data.qa.passed && (
        <div
          data-testid="report-qa-banner"
          className="space-y-1.5 rounded-md border border-yellow/40 bg-yellow/10 p-3 text-sm text-yellow"
        >
          <div className="font-medium">{t("report.qaFailed")}</div>
          {failedChecks.length > 0 && (
            <div data-testid="report-qa-gaps" className="space-y-1 text-[12.5px]">
              <span className="font-mono text-[10px] uppercase tracking-wider opacity-80">
                {t("report.qaGapIntro")}
              </span>
              {failedChecks.map((c) => (
                <div key={c.check} className="flex flex-wrap items-center gap-1.5">
                  <span>{t(`report.qaChecks.${c.check}`, { defaultValue: c.check })}</span>
                  {c.failed_ids.map((vid) => (
                    <button
                      key={vid}
                      type="button"
                      data-testid="qa-gap-vuln"
                      onClick={() => locateVuln(vid)}
                      className="rounded-full border border-yellow/50 px-1.5 py-0.5 font-mono text-[10.5px] transition-colors hover:bg-yellow/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-yellow"
                    >
                      {vid}
                    </button>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      <div className="space-y-4">
        <div className="flex items-center justify-end gap-1.5">
          <button
            type="button"
            data-testid="collapse-all"
            onClick={collapseAll}
            title={t("report.collapseAll")}
            className="inline-flex items-center gap-1 rounded-sm border border-border px-2 py-1 font-mono text-[10.5px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
          >
            <ListCollapse className="size-3.5" aria-hidden="true" />
            {t("report.collapseAll")}
          </button>
          <button
            type="button"
            data-testid="expand-all"
            onClick={expandAll}
            title={t("report.expandAll")}
            className="inline-flex items-center gap-1 rounded-sm border border-border px-2 py-1 font-mono text-[10.5px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
          >
            <ListTree className="size-3.5" aria-hidden="true" />
            {t("report.expandAll")}
          </button>
        </div>
        {data.vulnerabilities.map((v) => (
          <VulnerabilityCard
            key={v.id}
            v={v}
            collapsed={collapsed.has(v.id)}
            onToggleCollapse={() =>
              setCollapsed((prev) => {
                const next = new Set(prev);
                if (next.has(v.id)) next.delete(v.id);
                else next.add(v.id);
                return next;
              })
            }
          />
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
        <ReportToc data={data} onLocate={locateVuln} />
      </aside>
      {body}
    </div>
  );
}
