// 报告目录（2026-08-26）：结构化报告路径（ReportView）此前无目录——长报告几十张漏洞卡
// 只能盲滚。左栏 sticky 目录镜像页面区块：执行摘要 / 漏洞 (N)（severity 状态点 + ID +
// 标题小字）/ 攻击链。点击 → focusAnchor 精准落点（运行时量 sticky 遮蔽带，卡头标题
// 完整露出）+ coral 描边闪烁；scrollspy 高亮当前卡（对齐 dataflow TocSideBar 模式：
// 文档序第一个仍可见者）。视觉语言与 TocSideBar 同族（两行式条目 + 分组小字头），
// severity 点用 --c-* 语义色（与卡 SEV_DOT 同源）——扫目录即知严重度分布。
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ReportData } from "@/api/types";
import { focusAnchor } from "@/utils/focusAnchor";

/** 执行摘要 / 攻击链区锚点 id（ReportView 对应 section 挂载，与漏洞卡 id=vuln_id 同域）。 */
export const REPORT_EXEC_SUMMARY_ID = "report-exec-summary";
export const REPORT_CHAINS_ID = "report-chains";

/** severity（小写）→ 状态点色（--c-* 语义 channel，与卡 SEV_DOT 同源）。 */
const SEV_DOT_C: Record<string, string> = {
  critical: "hsl(var(--c-red))",
  high: "hsl(var(--c-orange))",
  medium: "hsl(var(--c-yellow))",
  low: "hsl(var(--muted-foreground))",
};

export function ReportToc({
  data,
  onLocate,
}: {
  data: ReportData;
  /** 跳转钩子（单源化 §7 折叠联动）：ReportView 传「先展开目标卡再定位」；
   *  缺省行为不变（直接 focusAnchor）——独立渲染（旧测试）零影响。 */
  onLocate?: (id: string) => void;
}) {
  const { t } = useTranslation();
  const [activeId, setActiveId] = useState<string | null>(null);
  const visibleRef = useRef<Set<string>>(new Set());

  const anchorIds = useMemo(
    () => [
      ...(data.executive_summary ? [REPORT_EXEC_SUMMARY_ID] : []),
      ...data.vulnerabilities.map((v) => v.id),
      ...(data.attack_chains.length > 0 ? [REPORT_CHAINS_ID] : []),
    ],
    [data],
  );

  // scrollspy：观察右内容区锚点，按文档序取第一个仍可见者高亮（多卡同屏时顶部优先）。
  // jsdom 无 IntersectionObserver → 跳过（不影响 TOC 渲染与点击定位）。
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const els = anchorIds
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => el !== null);
    if (els.length === 0) return;
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) visibleRef.current.add(e.target.id);
          else visibleRef.current.delete(e.target.id);
        }
        const first = anchorIds.find((id) => visibleRef.current.has(id));
        if (first) setActiveId(first);
      },
      // 视口上 10%~40% 带内命中才算「当前区块」（读者视线区）
      { rootMargin: "-10% 0px -60% 0px" },
    );
    for (const el of els) io.observe(el);
    return () => io.disconnect();
  }, [anchorIds]);

  const locate = (id: string) => {
    setActiveId(id); // 立即置高亮：smooth 滚动期间不等 scrollspy 回填
    if (onLocate) onLocate(id);
    else focusAnchor(id);
  };

  const itemCls = (id: string) =>
    `flex w-full items-start gap-1.5 rounded-sm p-1.5 text-left ${
      activeId === id ? "bg-accent text-accent-foreground" : "hover:bg-accent/60"
    }`;

  return (
    <nav aria-label={t("report.tocAria")} data-testid="report-toc" className="space-y-4 text-sm">
      {data.executive_summary && (
        <button
          type="button"
          data-toc-id={REPORT_EXEC_SUMMARY_ID}
          aria-current={activeId === REPORT_EXEC_SUMMARY_ID ? "true" : undefined}
          onClick={() => locate(REPORT_EXEC_SUMMARY_ID)}
          className={itemCls(REPORT_EXEC_SUMMARY_ID)}
        >
          <span className="truncate text-[13px] font-medium">{t("report.execSummary")}</span>
        </button>
      )}

      {data.vulnerabilities.length > 0 && (
        <div className="space-y-1">
          <p className="px-1.5 text-xs font-medium text-muted-foreground">
            {t("report.tocGroupVulns", { count: data.vulnerabilities.length })}
          </p>
          {data.vulnerabilities.map((v) => (
            <button
              key={v.id}
              type="button"
              data-toc-id={v.id}
              data-severity={v.severity ?? ""}
              aria-current={activeId === v.id ? "true" : undefined}
              onClick={() => locate(v.id)}
              className={itemCls(v.id)}
            >
              <span
                aria-hidden
                className="mt-1 size-1.5 shrink-0 rounded-full"
                style={{
                  background: SEV_DOT_C[(v.severity ?? "").toLowerCase()] ?? "hsl(var(--muted-foreground))",
                }}
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-mono text-[11.5px] font-medium text-foreground/90">{v.id}</span>
                {v.title && (
                  <span className="block truncate text-[11px] text-muted-foreground">{v.title}</span>
                )}
              </span>
            </button>
          ))}
        </div>
      )}

      {data.attack_chains.length > 0 && (
        <div className="space-y-1">
          <p className="px-1.5 text-xs font-medium text-muted-foreground">
            {t("report.tocGroupChains", { count: data.attack_chains.length })}
          </p>
          <button
            type="button"
            data-toc-id={REPORT_CHAINS_ID}
            aria-current={activeId === REPORT_CHAINS_ID ? "true" : undefined}
            onClick={() => locate(REPORT_CHAINS_ID)}
            className={`${itemCls(REPORT_CHAINS_ID)} font-mono text-[12px]`}
          >
            {t("report.attackChains")}
          </button>
        </div>
      )}
    </nav>
  );
}
