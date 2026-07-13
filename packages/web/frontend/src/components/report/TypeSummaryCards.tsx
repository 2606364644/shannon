import { useTranslation } from "react-i18next";
import { SEVERITY_BG, SEVERITY_TEXT, type TypeAgg } from "@/lib/report-stats";

/**
 * 类型汇总卡（对齐设计稿 report-a `.types`，配色换纯 severity）。
 * 每张卡：顶部 2px 色条 = 该类型最高 severity 色（严重类型显红）+ 类型名 + 大数字 count +
 * severity range 文字着色 + findings（可选）。
 */
export function TypeSummaryCards({ typeAggs }: { typeAggs: TypeAgg[] }) {
  const { t } = useTranslation();
  if (typeAggs.length === 0) return null;
  return (
    <section
      data-testid="type-summary-cards"
      aria-label={t("report.typeSummaryAria")}
      className="grid grid-cols-2 gap-3 md:grid-cols-5"
    >
      {typeAggs.map((t) => (
        <article
          key={t.prefix}
          data-testid="type-card"
          data-prefix={t.prefix}
          className="relative overflow-hidden rounded-md border border-border bg-card p-3.5 shadow-[var(--shadow-card)]"
        >
          <div
            data-testid="type-card-stripe"
            className={`absolute inset-x-0 top-0 h-0.5 ${SEVERITY_BG[t.severityRange.max]}`}
            aria-hidden="true"
          />
          <div className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
            {t.displayName}
          </div>
          <div className="mt-1.5 text-[34px] font-bold leading-none tracking-tight">
            {t.count}
          </div>
          <div className="mb-2 mt-1 font-mono text-[10.5px]">
            {t.count === 0 ? (
              <span className="text-muted-foreground">{t.severityRangeLabel}</span>
            ) : t.severityRange.min === t.severityRange.max ? (
              <span className={SEVERITY_TEXT[t.severityRange.max]}>{t.severityRange.max}</span>
            ) : (
              <>
                <span className={SEVERITY_TEXT[t.severityRange.max]}>{t.severityRange.max}</span>
                {" ~ "}
                <span className={SEVERITY_TEXT[t.severityRange.min]}>{t.severityRange.min}</span>
              </>
            )}
          </div>
          {t.findingsText && (
            <p className="border-t border-dashed border-border pt-2 text-[11.5px] leading-snug text-foreground/85">
              {t.findingsText}
            </p>
          )}
        </article>
      ))}
    </section>
  );
}
