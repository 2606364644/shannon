import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChevronDown } from "lucide-react";

/**
 * 攻击链独立章节（attack-chain agent 产的 `llm-chain-N`，多步利用路径）。
 *
 * 架构语义（见 spec 2026-07-14-report-attack-chain-section-design §2）：攻击链 ≠ 单点漏洞，
 * 与单漏洞卡片网格**分开渲染**。本组件承接 `splitAttackChainSection` 切出的攻击链章节内容，
 * 把每条 `### llm-chain-N: <标题>` 拆成**独立可折叠卡片**（对齐单漏洞卡片风格——每条都有小标题），
 * 章节级 h2 标题 + 计数徽章保留。
 */

interface ChainBlock {
  /** llm-chain-N */
  id: string;
  /** 标题行冒号后的描述文本 */
  title: string;
  /** 标题行之后的正文（字段 / 步骤 / 描述） */
  bodyMd: string;
}

interface ChainSplit {
  /** 第一个 llm-chain 标题之前的引导文字（若有）——原样 prose 渲染，不丢信息 */
  preamble: string;
  chains: ChainBlock[];
}

const CHAIN_HEADING_RE = /^### (llm-chain-\d+)\s*[:：—\-]?\s*(.*)$/;

/** 把攻击链章节 md 按 `### llm-chain-N` 切成多条；切不出条目时整段作 preamble（老报告兼容）。 */
function splitChains(md: string): ChainSplit {
  const lines = md.split(/\r?\n/);
  const preamble: string[] = [];
  const chains: ChainBlock[] = [];
  let cur: { id: string; title: string; body: string[] } | null = null;
  for (const ln of lines) {
    const m = CHAIN_HEADING_RE.exec(ln);
    if (m) {
      if (cur) chains.push({ id: cur.id, title: cur.title, bodyMd: cur.body.join("\n").trim() });
      cur = { id: m[1], title: m[2].trim(), body: [] };
    } else if (cur) {
      cur.body.push(ln);
    } else {
      preamble.push(ln);
    }
  }
  if (cur) chains.push({ id: cur.id, title: cur.title, bodyMd: cur.body.join("\n").trim() });
  return { preamble: preamble.join("\n").trim(), chains };
}

export function AttackChainSection({ md, count }: { md: string; count: number }) {
  const { t } = useTranslation();
  const { preamble, chains } = useMemo(() => splitChains(md), [md]);
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(() => new Set());
  const toggle = (id: string) =>
    setCollapsedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <section
      id="attack-chain-section"
      data-testid="attack-chain-section"
      aria-label={t("report.attackChains")}
      className="mt-6 scroll-mt-20 space-y-3"
    >
      {/* 章节标题：醒目 h2 + 计数徽章 */}
      <div className="flex items-center gap-2 border-b border-border pb-2">
        <h2 className="text-lg font-semibold tracking-tight text-foreground">{t("report.attackChains")}</h2>
        <span
          data-testid="attack-chain-count"
          className="rounded-full border border-border bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground"
        >
          {count}
        </span>
      </div>

      {/* 引导文字（链标题之前的描述）——原样渲染，不丢 */}
      {preamble && (
        <div className="prose prose-sm max-w-none break-words text-sm text-foreground prose-headings:font-sans">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{preamble}</ReactMarkdown>
        </div>
      )}

      {/* 每条攻击链一张可折叠卡片：ID（coral 主色）+ 描述小标题 + chevron，正文默认展开 */}
      {chains.map((chain) => {
        const collapsed = collapsedIds.has(chain.id);
        return (
          <section
            key={chain.id}
            id={chain.id}
            data-testid="chain-card"
            className="scroll-mt-20 rounded-md border border-border bg-card p-4 shadow-[var(--shadow-card)]"
          >
            <button
              type="button"
              data-testid="chain-toggle"
              onClick={() => toggle(chain.id)}
              aria-expanded={!collapsed}
              className="flex w-full min-w-0 items-center gap-2.5 text-left"
            >
              <span className="shrink-0 font-mono text-[13px] font-semibold text-primary">{chain.id}</span>
              {chain.title && (
                <span data-testid="chain-title" className="min-w-0 truncate text-[13px] font-medium text-foreground">{chain.title}</span>
              )}
              <ChevronDown
                className={`ml-auto size-4 shrink-0 text-muted-foreground transition-transform duration-150 ${collapsed ? "-rotate-90" : ""}`}
                aria-hidden="true"
              />
            </button>
            {!collapsed && chain.bodyMd && (
              <div className="prose prose-sm mt-3 max-w-none break-words prose-headings:font-sans">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{chain.bodyMd}</ReactMarkdown>
              </div>
            )}
          </section>
        );
      })}
    </section>
  );
}
