import { useMemo, useState, useEffect, useRef, Children, type ReactNode, type ReactElement } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import GithubSlugger from "github-slugger";
import remarkGfm from "remark-gfm";
import { visit } from "unist-util-visit";
import { toString } from "hast-util-to-string";
import { Button } from "@/components/ui/button";
import { ChevronDown, ChevronRight } from "lucide-react";
import { MarkdownVulnCard } from "./MarkdownVulnCard";
import { AttackChainSection } from "./report/AttackChainSection";
import { ThreatOverview } from "./report/ThreatOverview";
import { TypeSummaryCards } from "./report/TypeSummaryCards";
import { splitByVulnBlocks, inferSeverity, type Segment } from "@/lib/vuln-block";
import { splitAttackChainSection } from "@/lib/report-sections";
import {
  computeStats,
  type ParsedTypeSummary,
  type TopRiskItem,
} from "@/lib/report-stats";
import type { ParsedVulnBlock } from "../api/types";

interface Heading {
  id: string;
  text: string;
  level: 1 | 2 | 3;
}

/** TOC 条目：id 直接取自渲染后 DOM（见 makeSharedSlugPlugin），保证 href 永远命中。 */
interface TocItem {
  id: string;
  text: string;
  level: 1 | 2;
}

/**
 * 段级 slug rehype plugin：每个 prose 段（独立 ReactMarkdown 实例）用本函数生成专属
 * plugin。slugger 在 attacher 内部新建——每次组件渲染 plugin 重新 attach 时纯函数式
 * 重建，同样输入 → 同样 id，无跨渲染累积。segmentIndex 前缀（= group 在 groups 里的
 * 稳定索引）跨段保证全局唯一，避开「共享 slugger 在 React 渲染中可变状态不稳定」的
 * 陷阱：共享 slugger 会在严格模式/重渲染（如切主题）下被重复消费 → 同一标题拿到 -1
 * 后缀 → DOM id 漂移、与 TOC 错位（用户报告的「点了没反应」真根因）。
 */
function makeSegmentSlugPlugin(segmentIndex: number) {
  return function segmentSlug() {
    const slugger = new GithubSlugger();
    return (tree: any) => {
      visit(tree, "element", (node: any) => {
        if (typeof node.tagName === "string" && /^h[1-6]$/.test(node.tagName)) {
          node.properties = node.properties || {};
          node.properties.id = `s${segmentIndex}-${slugger.slug(toString(node))}`;
        }
      });
    };
  };
}

/** 从「INJ-VULN-01/02/03」这类文本提取完整 vuln ID（展开 /02 /03，复用 prefix）。 */
export function extractVulnIds(text: string): string[] {
  const ids: string[] = [];
  const re = /\b([A-Z]+)-VULN-(\d+)((?:\/\d+)*)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const prefix = m[1];
    ids.push(`${prefix}-VULN-${m[2]}`);
    if (m[3]) {
      for (const slashNum of m[3].matchAll(/\/(\d+)/g)) {
        ids.push(`${prefix}-VULN-${slashNum[1]}`);
      }
    }
  }
  return ids;
}

/** 从 md 提取 headings + 执行摘要「最高风险发现」+「按类型汇总」结构。
 *  注意：TOC 不再消费这里的 headings.id（改从 DOM 读真实 id），仅 topRisks /
 *  typeSummaries / execH2 检测依赖本函数。 */
function parseStructure(md: string): {
  headings: Heading[];
  topRisks: TopRiskItem[];
  typeSummaries: ParsedTypeSummary[];
} {
  const headings: Heading[] = [];
  const slugger = new GithubSlugger();
  const topRisks: TopRiskItem[] = [];
  const typeSummaries: ParsedTypeSummary[] = [];
  const lines = md.split(/\r?\n/);
  let inExecSummary = false;
  let inNumberedList = false;
  let inTypeSummarySection = false;
  let currentType: ParsedTypeSummary | null = null;

  const flushType = () => {
    if (currentType) {
      typeSummaries.push(currentType);
      currentType = null;
    }
  };

  for (const line of lines) {
    const hm = /^(#{1,3})\s+(.+)$/.exec(line);
    if (hm) {
      const level = hm[1].length as 1 | 2 | 3;
      const text = hm[2].trim();
      const id = slugger.slug(text);
      headings.push({ id, text, level });
      // 任何标题都关闭编号列表与当前类型小节
      inNumberedList = false;
      flushType();
      inExecSummary = text.includes("执行摘要");
      if (level <= 2) {
        inTypeSummarySection = text.includes("按漏洞类型汇总");
      } else if (inTypeSummarySection) {
        currentType = { prefix: "", displayName: text, count: 0, severityRangeRaw: "" };
      }
      continue;
    }

    if (inExecSummary) {
      const nm = /^\d+\.\s+(.+)$/.exec(line.trim());
      if (nm) {
        inNumberedList = true;
        const text = nm[1].replace(/\*\*/g, "");
        const vulnIds = extractVulnIds(text);
        topRisks.push({ text, vulnIds });
      } else if (inNumberedList && line.trim() && !/^\d+\./.test(line.trim())) {
        inNumberedList = false;
      }
      continue;
    }

    if (inTypeSummarySection && currentType) {
      const t = line.trim();
      const cm = /^(?:-\s*\*\*)?Count[:：]\s*\*\*\s*(\d+)/i.exec(t);
      if (cm) {
        currentType.count = parseInt(cm[1], 10);
        const pm = /（([A-Z]+)-(?:VULN|GN)/.exec(t);
        if (pm) currentType.prefix = pm[1];
        continue;
      }
      const sm = /^(?:-\s*\*\*)?Severity range[:：]\s*\*\*\s*(.+)$/i.exec(t);
      if (sm) {
        currentType.severityRangeRaw = sm[1].trim();
        continue;
      }
      const fm = /^(?:-\s*\*\*)?Key findings[:：]\s*\*\*\s*(.+)$/i.exec(t);
      if (fm) {
        currentType.findingsText = fm[1].trim();
        continue;
      }
    }
  }
  flushType();
  return { headings, topRisks, typeSummaries };
}

/** 拍平 ReactNode 到纯文本（用于键值检测）。 */
function flatten(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flatten).join("");
  const el = node as ReactElement<{ children?: ReactNode }>;
  if (el?.props?.children) return flatten(el.props.children);
  return "";
}

/** prose 段共享的 react-markdown 组件覆写（kv-row li / inline code / pre 复制按钮）。
 *  工厂接收 t：复制按钮文案随语言切换（react-markdown 的 components 项不订阅 i18n，
 *  靠外层 MarkdownView 的 useTranslation 触发重渲染、传入最新 t）。 */
function makeProseComponents(t: TFunction) {
  return {
  // KV 行（冒号守卫：`- **key:** value` → kv-row；编号列表 `1. **RCE**…` 不匹配）
  li: ({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) => {
    const kids = Array.isArray(children) ? children : [children];
    const firstStrongIdx = kids.findIndex(
      (k) => typeof k !== "string" && (k as ReactElement)?.type === "strong",
    );
    if (firstStrongIdx !== -1) {
      const strongEl = kids[firstStrongIdx] as ReactElement<{ children?: ReactNode }>;
      const rawKey = flatten(strongEl.props.children);
      if (!/[：:]\s*$/.test(rawKey)) {
        return <li {...props}>{children}</li>;
      }
      const keyText = rawKey.replace(/[:：]\s*$/, "").trim();
      if (keyText) {
        const restKids = kids.slice(firstStrongIdx + 1);
        const valKids: ReactNode[] = [];
        let trimming = true;
        for (const k of restKids) {
          if (trimming && typeof k === "string" && /^\s*$/.test(k)) continue;
          if (trimming && typeof k === "string") {
            valKids.push(k.replace(/^\s+/, ""));
            trimming = false;
          } else {
            valKids.push(k);
            trimming = false;
          }
        }
        return (
          <li {...props} data-testid="kv-row" className="flex items-baseline gap-2">
            <span className="kv-key shrink-0 font-mono text-muted-foreground">{keyText}</span>
            <span className="kv-val">{valKids}</span>
          </li>
        );
      }
    }
    return <li {...props}>{children}</li>;
  },
  // block code：仅渲染 <code>（含 hljs language-xxx class），装饰交给 pre
  code: ({ className, children, ...props }: { className?: string; children?: ReactNode; [k: string]: unknown }) => (
    <code {...props} className={`font-mono ${className ?? ""}`}>{children}</code>
  ),
  // pre：只包 block code → 加语言角标 + 复制按钮
  pre: ({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) => {
    const codeChild = Children.toArray(children)[0] as ReactElement<{
      className?: string;
      children?: ReactNode;
    }>;
    const cls = (codeChild?.props as { className?: string } | undefined)?.className ?? "";
    const lang = /language-(\w+)/.exec(cls)?.[1] ?? "";
    const text = flatten(codeChild?.props?.children);
    return (
      <pre {...props} data-testid="code-block" className="relative">
        {lang && (
          <span
            data-testid="code-lang"
            className="absolute right-2 top-1 font-mono text-xs text-muted-foreground"
          >
            {lang}
          </span>
        )}
        <Button
          size="sm"
          variant="ghost"
          data-testid="copy-btn"
          className="copy-btn absolute right-2 bottom-1 text-xs opacity-60 hover:opacity-100"
          onClick={(e) => {
            navigator.clipboard?.writeText(text);
            e.currentTarget.textContent = "✓";
          }}
        >
          {t("markdown.copy")}
        </Button>
        {children}
      </pre>
    );
  },
  };
}

const REMARK_PLUGINS = [remarkGfm];

/** 连续 vuln 段合并成一个 grid 组，prose 段单独成组。 */
type VulnGroup = { type: "prose"; md: string } | { type: "grid"; blocks: ParsedVulnBlock[] };
function groupSegments(segments: Segment[]): VulnGroup[] {
  const groups: VulnGroup[] = [];
  let vulnAccum: ParsedVulnBlock[] = [];
  for (const seg of segments) {
    if (seg.type === "vuln") {
      vulnAccum.push(seg.block);
    } else {
      if (vulnAccum.length) {
        groups.push({ type: "grid", blocks: vulnAccum });
        vulnAccum = [];
      }
      groups.push({ type: "prose", md: seg.md });
    }
  }
  if (vulnAccum.length) groups.push({ type: "grid", blocks: vulnAccum });
  return groups;
}

export function MarkdownView({ markdown }: { markdown: string }) {
  const { t } = useTranslation();
  const [heroCollapsed, setHeroCollapsed] = useState(false);
  const proseComponents = useMemo(() => makeProseComponents(t), [t]);
  const { headings, topRisks, typeSummaries } = useMemo(() => parseStructure(markdown), [markdown]);
  const execH2 = headings.find((h) => h.text.includes("执行摘要"));
  const showHero = !!execH2 && topRisks.length > 0;
  const topRiskIds = useMemo(() => {
    const s = new Set<string>();
    for (const r of topRisks) for (const id of r.vulnIds) s.add(id);
    return s;
  }, [topRisks]);
  // 攻击链章节独立切出（架构语义：攻击链 ≠ 单点漏洞，分开渲染/计数，见 spec §2/§5）。
  // splitByVulnBlocks 只对「去掉攻击链章节后的 md」切单点漏洞，避免攻击链内容进 vuln 切分。
  const attackChainSplit = useMemo(() => splitAttackChainSection(markdown), [markdown]);
  const singleVulnMd = attackChainSplit
    ? attackChainSplit.before + attackChainSplit.after
    : markdown;
  const segments = useMemo(() => splitByVulnBlocks(singleVulnMd), [singleVulnMd]);
  const stats = useMemo(
    () => ({
      ...computeStats(
        segments
          .filter((s): s is Extract<Segment, { type: "vuln" }> => s.type === "vuln")
          .map((s) => s.block),
        topRiskIds,
        topRisks,
        typeSummaries,
      ),
      attackChainCount: attackChainSplit?.count ?? 0,
    }),
    [segments, topRiskIds, topRisks, typeSummaries, attackChainSplit],
  );
  const groups = useMemo(() => groupSegments(segments), [segments]);

  // TOC：从渲染后 DOM 读真实 heading id（h1 章节为骨架 + h2 子节；vuln h3 太碎不进 TOC）。
  // 这保证每个 TOC href 都命中 DOM 真实元素，与 id 生成方式解耦——根治「点了没反应」。
  const contentRef = useRef<HTMLDivElement>(null);
  const [tocItems, setTocItems] = useState<TocItem[]>([]);
  useEffect(() => {
    const root = contentRef.current;
    if (!root) {
      setTocItems([]);
      return;
    }
    const items: TocItem[] = [];
    root.querySelectorAll<HTMLElement>("h1[id], h2[id]").forEach((el) => {
      const id = el.id;
      if (!id) return;
      const level = (el.tagName === "H1" ? 1 : 2) as 1 | 2;
      const text = (el.textContent || "").replace(/\s+/g, " ").trim();
      if (text) items.push({ id, text, level });
    });
    setTocItems(items);
  }, [markdown, groups]);

  // scroll-spy：高亮当前可视章节。jsdom 无 IntersectionObserver → 跳过（不影响 TOC 渲染）。
  const [activeId, setActiveId] = useState<string>("");
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined" || tocItems.length === 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]?.target.id) setActiveId(visible[0].target.id);
      },
      { rootMargin: "-80px 0px -70% 0px", threshold: 0 },
    );
    for (const { id } of tocItems) {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [tocItems]);

  const twoCol = tocItems.length >= 2;

  return (
    <div className="space-y-5">
      <ThreatOverview stats={stats} />

      {showHero && (
        <div
          data-testid="exec-summary-hero"
          className="rounded-md border border-border border-l-2 border-l-red/60 bg-card p-4 shadow-[var(--shadow-card)]"
        >
          <div className="mb-2 flex items-center justify-between font-semibold tracking-tight text-base">
            <span>{t("markdown.topRisksTitle")}</span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setHeroCollapsed((c) => !c)}
              aria-label={t("markdown.toggleHeroAria")}
            >
              {heroCollapsed ? <ChevronRight className="size-4" /> : <ChevronDown className="size-4" />}
              <span className="sr-only">{heroCollapsed ? t("markdown.expand") : t("markdown.collapse")}</span>
            </Button>
          </div>
          {!heroCollapsed && (
            <ol className="list-decimal space-y-1 pl-6 text-sm">
              {topRisks.map((r, i) => (
                <li key={i}>
                  {r.vulnIds.length > 0 && (
                    <a
                      href={`#${r.vulnIds[0]}`}
                      className="kv-vuln-id font-mono text-primary"
                    >
                      {r.vulnIds.join("/")}
                    </a>
                  )}{" "}
                  {r.text}
                </li>
              ))}
            </ol>
          )}
        </div>
      )}

      <TypeSummaryCards typeAggs={stats.typeAggs} />

      <div className={twoCol ? "grid grid-cols-[200px_1fr] gap-8" : "grid grid-cols-1"}>
        {twoCol && (
          <nav data-testid="toc" aria-label={t("markdown.tocAria")} className="sticky top-4 self-start">
            <div className="mb-2 px-2 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              {t("markdown.toc")}
            </div>
            <ul className="space-y-0.5">
              {tocItems.map((h, i) => {
                const active = h.id === activeId;
                return (
                  <li key={`${i}-${h.id}`}>
                    <a
                      href={`#${h.id}`}
                      className={`group flex items-center gap-2 rounded-md px-2 py-1.5 text-[13px] transition-colors ${
                        h.level === 2 ? "pl-7" : ""
                      } ${
                        active
                          ? "bg-accent text-foreground"
                          : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                      }`}
                    >
                      <span
                        className={`h-1 w-1 shrink-0 rounded-full transition-colors ${
                          active ? "bg-primary" : "bg-transparent group-hover:bg-muted-foreground/60"
                        }`}
                        aria-hidden="true"
                      />
                      <span className="truncate">{h.text}</span>
                    </a>
                  </li>
                );
              })}
            </ul>
          </nav>
        )}
        <div ref={contentRef} className="space-y-5">
          {groups.map((g, i) =>
            g.type === "prose" ? (
              <div
                key={i}
                className="prose prose-sm max-w-none font-sans prose-headings:font-sans prose-headings:tracking-tight prose-h1:mt-0"
              >
                <ReactMarkdown
                  remarkPlugins={REMARK_PLUGINS}
                  rehypePlugins={[makeSegmentSlugPlugin(i), rehypeHighlight] as never}
                  components={proseComponents as never}
                >
                  {g.md}
                </ReactMarkdown>
              </div>
            ) : (
              <div
                key={i}
                data-testid="vuln-grid"
                className="grid grid-cols-1 gap-3 lg:grid-cols-2"
              >
                {g.blocks.map((block) => (
                  <MarkdownVulnCard
                    key={block.id}
                    block={block}
                    severity={inferSeverity(block, topRiskIds)}
                  />
                ))}
              </div>
            ),
          )}
          {attackChainSplit && (
            <AttackChainSection md={attackChainSplit.sectionMd} count={attackChainSplit.count} />
          )}
        </div>
      </div>
    </div>
  );
}
