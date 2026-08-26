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
        <table className="w-full min-w-[560px] border-collapse text-left">
          <thead>
            <tr className="border-b border-border font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              <th className="px-2.5 py-1.5 font-medium">ID</th>
              <th className="px-2.5 py-1.5 font-medium">{t("report.quickRefTitle")}</th>
              <th className="px-2.5 py-1.5 font-medium">{t("report.colParams")}</th>
              <th className="px-2.5 py-1.5 font-medium">{t("report.quickRefEndpoints")}</th>
              <th className="px-2.5 py-1.5 font-medium">{t("report.quickRefSeverity")}</th>
              <th className="px-2.5 py-1.5 font-medium">{t("report.quickRefVerification")}</th>
              <th className="px-2.5 py-1.5 font-medium">{t("report.quickRefConfidence")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const sev = (r.severity ?? "").toLowerCase();
              return (
                <tr
                  key={r.id}
                  data-testid="quick-ref-row"
                  className="cursor-pointer border-b border-border/50 transition-colors last:border-b-0 hover:bg-accent/40"
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
                      className="font-mono text-[11.5px] text-primary underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
                    >
                      {r.id}
                    </button>
                  </td>
                  <td className={`${cellCls} min-w-[140px] break-words text-foreground/90`}>
                    {r.title ?? "—"}
                  </td>
                  <td className={`${cellCls} text-muted-foreground`}>
                    {r.params.length > 0 ? r.params.join(", ") : "—"}
                  </td>
                  <td className={`${cellCls} font-mono text-[11px] text-muted-foreground`}>
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
                  <td className={`${cellCls} text-foreground/85`}>{r.verification ?? "—"}</td>
                  <td className={`${cellCls} text-foreground/85`}>{r.confidence ?? "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
