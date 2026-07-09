import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { ParsedVulnBlock, Severity } from "../api/types";

/** severity → 卡片边框 + 底色（走 --c-* alpha，深/浅主题自动重算）。
 *  暖色梯度 red→orange→yellow→灰；cyan 留给「公网」等信息标签不进 severity。 */
const SEVERITY_BORDER: Record<Severity, string> = {
  Critical: "border-red/50 bg-red/5",
  High: "border-orange/50 bg-orange/5",
  Medium: "border-yellow/50 bg-yellow/5",
  Low: "border-border",
};

/** severity → 左侧色条。 */
const SEVERITY_STRIPE: Record<Severity, string> = {
  Critical: "bg-red",
  High: "bg-orange",
  Medium: "bg-yellow",
  Low: "bg-muted-foreground",
};

/** severity → id chip / 角标 文字+背景+边框。 */
const SEVERITY_CHIP: Record<Severity, string> = {
  Critical: "text-red bg-red/10 border-red/30",
  High: "text-orange bg-orange/10 border-orange/30",
  Medium: "text-yellow bg-yellow/10 border-yellow/30",
  Low: "text-muted-foreground bg-muted border-border",
};

/** 把 val 文本里的 `code` 段渲染成 <code>（轻量 inline code 解析）。 */
function renderInline(text: string, keyBase: string): ReactNode[] {
  return text.split(/(`[^`]+`)/g).map((p, i) => {
    if (p.startsWith("`") && p.endsWith("`") && p.length >= 2) {
      return (
        <code key={`${keyBase}-${i}`} className="font-mono text-[11px] bg-muted px-1 rounded-sm text-primary">
          {p.slice(1, -1)}
        </code>
      );
    }
    return <span key={`${keyBase}-${i}`}>{p}</span>;
  });
}

/**
 * 漏洞卡片（消费从 markdown 解析出的 ParsedVulnBlock）。
 * severity 由父组件用 inferSeverity 算好后传入（本组件只管渲染）。
 * PoC 折叠复用 VulnCard 的手写模式（useState + role=button + aria-expanded + Enter/Space）。
 */
export function MarkdownVulnCard({ block, severity }: { block: ParsedVulnBlock; severity: Severity }) {
  const { t } = useTranslation();
  const [pocOpen, setPocOpen] = useState(false);
  const togglePoc = () => setPocOpen((o) => !o);

  return (
    <article
      data-testid="vuln-card"
      data-severity={severity}
      id={block.id || undefined}
      className={`flex overflow-hidden rounded-md border ${SEVERITY_BORDER[severity]}`}
    >
      <div className={`w-1 shrink-0 ${SEVERITY_STRIPE[severity]}`} aria-hidden="true" />
      <div className="flex-1 p-3">
        <header className="flex flex-wrap items-center gap-1.5 mb-1.5 font-mono text-[11px]">
          <span
            data-testid="vuln-id"
            className={`font-semibold px-2 py-0.5 rounded-full border ${SEVERITY_CHIP[severity]}`}
          >
            {block.id}
          </span>
          {block.starred && (
            <span className="px-1 py-0.5 rounded-sm border border-red/40 bg-red/10 text-red">{t("vuln.starred")}</span>
          )}
          {block.vulnType && (
            <span className="px-1 py-0.5 rounded-sm border border-border bg-muted text-muted-foreground">
              {block.vulnType}
            </span>
          )}
          {block.externallyExploitable === true && (
            <span className="text-cyan">{t("vuln.publicNet")}</span>
          )}
          {block.authRequired === false && (
            <span className="text-cyan">{t("vuln.preAuth")}</span>
          )}
          {block.authRequired === true && (
            <span className="text-muted-foreground">{t("vuln.authRequired")}</span>
          )}
          {block.confidence && (
            <span className="text-muted-foreground">{t("vuln.confidenceLabel")} {block.confidence}</span>
          )}
          <span
            data-testid="severity-badge"
            className={`ml-auto px-2 py-0.5 rounded-full border ${SEVERITY_CHIP[severity]}`}
            title={t("vuln.severityTooltip")}
          >
            {t(`vuln.severity.${severity}`, { defaultValue: severity })} <span className="opacity-60">{t("vuln.inferred")}</span>
          </span>
        </header>

        <h3 className="text-[13.5px] font-semibold leading-snug mb-2">{block.title}</h3>

        {block.fields.length > 0 && (
          <ul className="space-y-0.5">
            {block.fields.map((f, i) => (
              <li key={i} data-testid="kv-row" className="flex items-baseline gap-2 text-[12px]">
                <span className="kv-key shrink-0 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                  {f.key}
                </span>
                <span className="kv-val break-all">{renderInline(f.val, `kv-${i}`)}</span>
              </li>
            ))}
          </ul>
        )}

        {block.witnessPayload && (
          <div className="mt-2">
            <div
              role="button"
              tabIndex={0}
              aria-expanded={pocOpen}
              data-testid="poc-toggle"
              onClick={togglePoc}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  togglePoc();
                }
              }}
              className="inline-flex cursor-pointer items-center gap-1 rounded-sm border border-dashed border-border px-2 py-0.5 font-mono text-[10.5px] text-muted-foreground hover:text-foreground"
            >
              <span aria-hidden="true">{pocOpen ? "▾" : "▸"}</span> {t("vuln.pocWitness")}
            </div>
            {pocOpen && (
              <pre
                data-testid="poc-code"
                className="mt-1.5 overflow-x-auto rounded-sm border border-border border-l-2 border-l-red/60 bg-background p-2 font-mono text-[11px] text-green"
              >
                <code>{block.witnessPayload}</code>
              </pre>
            )}
          </div>
        )}
      </div>
    </article>
  );
}
