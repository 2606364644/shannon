import { useTranslation } from "react-i18next";
import type { QuickReferenceRow } from "@/api/types";
import { focusAnchor } from "@/utils/focusAnchor";

/** severity（小写）→ 药丸配色（与 VulnerabilityCard.SEV_PILL 同源暖色通道）。 */
const SEV_PILL: Record<string, string> = {
  critical: "bg-red/15 text-red",
  high: "bg-orange/15 text-orange",
  medium: "bg-yellow/15 text-yellow",
  low: "bg-muted text-muted-foreground",
};

/** severity（小写）→ 行左缘色规（与 VulnerabilityCard.SEV_EDGE 同语言）：行序是
 *  类序分组 + 类内降序（builder 定，与 md 同源不可重排），左缘色带让埋在后位类的
 *  Critical 与首行 Critical 同权重——长表扫左缘即得全局危险地图。 */
const SEV_EDGE: Record<string, string> = {
  critical: "border-l-2 border-l-red/70",
  high: "border-l-2 border-l-orange/70",
  medium: "border-l-2 border-l-yellow/70",
  low: "border-l-2 border-l-muted-foreground/40",
};

/** 动态验证判定（值域来自 report_assembler._verification_cell 映射：zh「已动态
 *  验证」/ en "Dynamically Verified" / 未知名原样透传）——含「动态」/ dynamic 即
 *  实锤信号，绿色提亮（对齐卡内 evidence dynamic 徽章语言）。 */
const isDynamicVerification = (v: string | null | undefined) =>
  !!v && (v.includes("动态") || v.toLowerCase().includes("dynamic"));

/** 置信度待复核判定（QA 风险信号，amber 弱提示）：zh 待复核/未判定、en
 *  Pending Review / Unadjudicated（report_assembler._confidence_cell 值域）。 */
const isReviewFlagged = (v: string | null | undefined) =>
  !!v &&
  (["待复核", "未判定"].some((k) => v.includes(k)) ||
    ["pending", "unadjudicated", "review"].some((k) => v.toLowerCase().includes(k)));

/** 参数列截断口径（对齐 md _params_cell）：>3 显示前 3 +「等 N 个」，title 悬停
 *  全量——交互介质的速查密度，数据仍全量同源（builder 注释：params 存全量原样，
 *  >3 截断是渲染层的事）。 */
const PARAMS_MAX = 3;

/**
 * 漏洞速查表节（spec 2026-08-26-report-single-source-rendering §5/§7）：吃
 * report_data.quick_reference——builder 确定性产（vulnerabilities +
 * affected_parameters），前端只渲染不派生（md 导出同源渲染同一行数据）。
 * 行内 ID button（键盘可达）→ onLocate(vuln_id) 跳转对应卡（折叠卡先展开）。
 */
export function QuickReferenceTable({
  rows,
  onLocate,
}: {
  rows: QuickReferenceRow[];
  onLocate?: (id: string) => void;
}) {
  const { t } = useTranslation();
  if (rows.length === 0) return null;
  const locate = (id: string) => {
    if (onLocate) onLocate(id);
    else focusAnchor(id);
  };
  const cellCls = "px-2.5 py-1.5 align-top text-[11.5px] leading-snug";

  return (
    <section
      data-testid="quick-reference"
      className="rounded-md border border-border bg-card p-4 shadow-[var(--shadow-card)]"
    >
      <h2 className="mb-3 text-base font-semibold tracking-tight">
        {t("report.quickReference")}
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] border-collapse text-left">
          <thead>
            <tr className="border-b border-border font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              <th scope="col" className="px-2.5 py-1.5 font-medium">ID</th>
              <th scope="col" className="px-2.5 py-1.5 font-medium">{t("report.quickRefTitle")}</th>
              <th scope="col" className="px-2.5 py-1.5 font-medium">{t("report.colParams")}</th>
              <th scope="col" className="px-2.5 py-1.5 font-medium">{t("report.quickRefEndpoints")}</th>
              <th scope="col" className="px-2.5 py-1.5 font-medium">{t("report.quickRefSeverity")}</th>
              <th scope="col" className="px-2.5 py-1.5 font-medium">{t("report.quickRefVerification")}</th>
              <th scope="col" className="px-2.5 py-1.5 font-medium">{t("report.quickRefConfidence")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const sev = (r.severity ?? "").toLowerCase();
              const paramsAll = r.params.join(", ");
              const paramsShown =
                r.params.length > PARAMS_MAX
                  ? `${r.params.slice(0, PARAMS_MAX).join(", ")}${t("report.paramsMoreSuffix", { n: r.params.length })}`
                  : paramsAll;
              return (
                <tr
                  key={r.id}
                  data-testid="quick-ref-row"
                  className={`cursor-pointer border-b border-border/50 transition-colors last:border-b-0 hover:bg-accent/40${sev in SEV_EDGE ? ` ${SEV_EDGE[sev]}` : ""}`}
                  onClick={() => locate(r.id)}
                >
                  <td className={`${cellCls} font-mono`}>
                    <button
                      type="button"
                      data-testid={`quick-ref-jump-${r.id}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        locate(r.id);
                      }}
                      title={r.id}
                      className="block max-w-[9rem] truncate font-mono text-[11.5px] text-primary underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
                    >
                      {r.id}
                    </button>
                  </td>
                  <td className={`${cellCls} min-w-[140px] break-words text-foreground/90`}>
                    {r.title ?? "—"}
                  </td>
                  <td
                    data-testid={`quick-ref-params-${r.id}`}
                    title={r.params.length > PARAMS_MAX ? paramsAll : undefined}
                    className={`${cellCls} break-words text-muted-foreground`}
                  >
                    {r.params.length > 0 ? paramsShown : "—"}
                  </td>
                  <td className={`${cellCls} break-words font-mono text-[11px] text-muted-foreground`}>
                    {r.endpoints.length > 0 ? r.endpoints.join(", ") : "—"}
                  </td>
                  <td className={cellCls}>
                    {sev in SEV_PILL ? (
                      <span
                        className={`inline-flex rounded-full px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide ${SEV_PILL[sev]}`}
                      >
                        {sev}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </td>
                  <td
                    data-testid={`quick-ref-verification-${r.id}`}
                    className={`${cellCls} whitespace-nowrap ${isDynamicVerification(r.verification) ? "text-green" : "text-muted-foreground"}`}
                  >
                    {r.verification ?? "—"}
                  </td>
                  <td
                    data-testid={`quick-ref-confidence-${r.id}`}
                    className={`${cellCls} whitespace-nowrap ${isReviewFlagged(r.confidence) ? "text-amber" : "text-foreground/85"}`}
                  >
                    {r.confidence ?? "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
