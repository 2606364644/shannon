import { useTranslation } from "react-i18next";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * 攻击链独立章节（attack-chain agent 产的 `llm-chain-N`，多步利用路径）。
 *
 * 架构语义（见 spec 2026-07-14-report-attack-chain-section-design §2）：攻击链 ≠ 单点漏洞，
 * 与单漏洞卡片网格**分开渲染**。本组件只承接 `splitAttackChainSection` 切出的攻击链章节内容，
 * 原样 markdown 渲染（`### llm-chain-N` 标题 + 利用 steps 叙述），不解析为 vuln block。
 *
 * md = 攻击链章节标题行**之后**的内容（不含 `## 攻击链` 标题行——标题由本组件渲染，避免重复）。
 */
export function AttackChainSection({ md, count }: { md: string; count: number }) {
  const { t } = useTranslation();
  return (
    <section
      data-testid="attack-chain-section"
      aria-label={t("report.attackChains")}
      className="mt-6 rounded-md border border-border bg-card p-4 shadow-[var(--shadow-card)]"
    >
      <div className="mb-3 flex items-center gap-2 border-b border-border pb-2">
        <h2 className="text-base font-semibold text-foreground">{t("report.attackChains")}</h2>
        <span
          data-testid="attack-chain-count"
          className="rounded-full border border-border bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground"
        >
          {count}
        </span>
      </div>
      <div className="prose prose-sm max-w-none text-sm text-foreground">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{md}</ReactMarkdown>
      </div>
    </section>
  );
}
