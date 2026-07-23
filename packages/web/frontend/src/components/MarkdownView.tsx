import { useMemo, useState, useEffect, useRef, Children, type ReactNode, type ReactElement, type MouseEvent } from "react";
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
import { AttackChainSection } from "./report/AttackChainSection";
import { ThreatOverview } from "./report/ThreatOverview";
import { TypeSummaryCards } from "./report/TypeSummaryCards";
import { splitByVulnBlocks, inferSeverity, type Segment } from "@/lib/vuln-block";
import { splitAttackChainSection, splitPocSection, parsePocEntries } from "@/lib/report-sections";
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
  level: 1 | 2 | 3;
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
      const cm = /^(?:-\s*\*\*)?(?:Count|数量)[:：]\s*\*?\*?\s*(\d+)/i.exec(t);
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
            <span className="kv-val min-w-0 break-words">{valKids}</span>
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

/** severity → 配色：报告里不同危害程度用不同颜色区分（左轨边框 / 底色微染 / 圆点 / 标签文字）。 */
// severity 配色：DSF 暖色语义通道 --c-red/orange/yellow（与 coral 主色同暖系，Claude 感）。
// 只在「药丸标签 + 圆点」上着色；卡片本体保持 bg-card + hairline + shadow-card 的 Claude 卡面
// （对齐 AttackChainSection，不搞 alert 式色条/底色）。
const SEV_PILL: Record<string, string> = {
  Critical: "bg-red/15 text-red",
  High: "bg-orange/15 text-orange",
  Medium: "bg-yellow/15 text-yellow",
  Low: "bg-muted text-muted-foreground",
};
const SEV_DOT: Record<string, string> = {
  Critical: "bg-red",
  High: "bg-orange",
  Medium: "bg-yellow",
  Low: "bg-muted-foreground",
};

/** 从漏洞块派生一行可扫的「是什么」小标题：优先 Sink/Location 的 basename:行号，其次 vulnType。
 *  GitNexus 轨漏洞标题只有 ID（无描述），靠这个给出有意义的扫描线索。 */
function vulnPreview(block: ParsedVulnBlock): string {
  const f =
    block.fields.find((x) => /sink call|sink/i.test(x.key)) ||
    block.fields.find((x) => /vulnerable location|location|source|endpoint/i.test(x.key));
  const raw = f?.val?.replace(/`/g, "") ?? "";
  const bm = /([^/()\s]+\.\w{1,5})/.exec(raw);
  const base = bm?.[1];
  if (base) {
    // basename 之后的第一个 :<数字> = 源/汇行号（路径形如 file.java:method:innercall:712:36）
    const after = raw.slice((bm?.index ?? 0) + base.length);
    const line = /:(\d+)/.exec(after)?.[1];
    return line ? `${base}:${line}` : base;
  }
  if (raw) return raw.split(/\s/)[0];
  return block.vulnType || "";
}

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
  // 默认全部展开（完整展示，不丢信息）。每张卡片可单独折叠（chevron）；粘性按钮批量「全部收起/展开」。
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(() => new Set());
  const toggleCard = (id: string) =>
    setCollapsedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const proseComponents = useMemo(() => makeProseComponents(t), [t]);
  const { headings, topRisks, typeSummaries } = useMemo(() => parseStructure(markdown), [markdown]);
  const execH2 = headings.find((h) => h.text.includes("执行摘要"));
  const showHero = !!execH2 && topRisks.length > 0;
  const topRiskIds = useMemo(() => {
    const s = new Set<string>();
    for (const r of topRisks) for (const id of r.vulnIds) s.add(id);
    return s;
  }, [topRisks]);
  // PoC 独立章节最先切出（后端 report endpoint 把「主报告 + --- + PoC md」拼一份；PoC 在最末）。
  // 切出后按 ID 并入对应漏洞卡片 body，不再独立成章（spec 2026-07-24 §3.1）。
  const pocSplit = useMemo(() => splitPocSection(markdown), [markdown]);
  const withoutPoc = pocSplit ? pocSplit.before : markdown;
  // 攻击链章节独立切出（架构语义：攻击链 ≠ 单点漏洞，分开渲染/计数，见 spec §2/§5）。
  // splitByVulnBlocks 只对「去掉 PoC + 攻击链章节后的 md」切单点漏洞。
  const attackChainSplit = useMemo(() => splitAttackChainSection(withoutPoc), [withoutPoc]);
  const singleVulnMd = attackChainSplit
    ? attackChainSplit.before + attackChainSplit.after
    : withoutPoc;
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
  const hasVulns = groups.some((g) => g.type === "grid");
  const allVulnIds = useMemo(
    () => groups.flatMap((g) => (g.type === "grid" ? g.blocks.map((b) => b.id) : [])),
    [groups],
  );
  const allCollapsed = allVulnIds.length > 0 && allVulnIds.every((id) => collapsedIds.has(id));

  // PoC → 漏洞卡片映射（spec 2026-07-24 §3.1）。重复 id 取首条。
  const pocEntries = useMemo(
    () => (pocSplit ? parsePocEntries(pocSplit.pocMd) : []),
    [pocSplit],
  );
  const pocById = useMemo(() => {
    const m = new Map<string, string>();
    for (const e of pocEntries) if (!m.has(e.id)) m.set(e.id, e.md);
    return m;
  }, [pocEntries]);
  // 已并入卡片的 PoC（有对应漏洞卡片）；无对应卡片的 PoC 末尾兜底列出，不丢信息。
  const matchedPocIds = useMemo(
    () => new Set(allVulnIds.filter((id) => pocById.has(id))),
    [allVulnIds, pocById],
  );
  const orphanPocEntries = useMemo(
    () => pocEntries.filter((e) => !matchedPocIds.has(e.id)),
    [pocEntries, matchedPocIds],
  );

  // 锚点跳转：只平滑滚动，不 focus 目标（避免浏览器原生锚点的 outline / 焦点跳动）。
  // 保留 href 供无障碍 / 键盘 / 右键复制（spec 2026-07-24 §3.2）。
  const scrollToId = (e: MouseEvent<HTMLAnchorElement>, id: string) => {
    e.preventDefault();
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  // 漏洞卡片「全部收起/展开」按钮（原 findings-bar 浮动条移除，挪进 TOC 侧栏；无 TOC 时降级非 sticky）。
  const collapseAllCardsBtn =
    hasVulns && allVulnIds.length > 0 ? (
      <button
        type="button"
        data-testid="vuln-expand-all"
        onClick={() => setCollapsedIds(allCollapsed ? new Set() : new Set(allVulnIds))}
        className="rounded px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground"
      >
        {allCollapsed ? t("markdown.expandCards") : t("markdown.collapseCards")}
      </button>
    ) : null;

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
    // 收集：h1/h2 章节标题 + 攻击链章节 + 单漏洞卡 / 攻击链条目（按 DOM 顺序，href 命中真实 id）
    root.querySelectorAll<HTMLElement>(
      "h1[id], h2[id], [data-testid='attack-chain-section'], [data-testid='vuln-card'][id], [data-testid='chain-card'][id]",
    ).forEach((el) => {
      const testid = el.getAttribute("data-testid");
      let level: 1 | 2 | 3;
      let id = el.id;
      let text = "";
      const clean = (s: string | null | undefined) => (s ?? "").replace(/\s+/g, " ").trim();
      if (el.tagName === "H1") {
        level = 1;
        text = clean(el.textContent);
      } else if (el.tagName === "H2") {
        level = 2;
        text = clean(el.textContent);
      } else if (testid === "attack-chain-section") {
        level = 2;
        id = "attack-chain-section";
        text = clean(el.querySelector("h2")?.textContent) || t("report.attackChains");
      } else if (testid === "chain-card") {
        level = 3;
        text = clean(el.querySelector('[data-testid="chain-title"]')?.textContent) || id;
      } else {
        // vuln-card：用 ID 作可扫条目
        level = 3;
        text = id;
      }
      if (id && text) items.push({ id, text, level });
    });
    setTocItems(items);
  }, [markdown, groups, t]);

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

  // TOC 树：把 level-3（漏洞/攻击链条目）挂到最近的 level<=2 父章节下，做成可折叠树。
  const tocTree = useMemo(() => {
    const tree: { item: TocItem; children: TocItem[] }[] = [];
    const childToParent: Record<string, string> = {};
    let cur: { item: TocItem; children: TocItem[] } | null = null;
    for (const it of tocItems) {
      if (it.level <= 2) {
        cur = { item: it, children: [] };
        tree.push(cur);
      } else if (cur) {
        cur.children.push(it);
        childToParent[it.id] = cur.item.id;
      }
    }
    return { tree, childToParent };
  }, [tocItems]);
  const [collapsedSections, setCollapsedSections] = useState<Set<string>>(() => new Set());
  const toggleSection = (id: string) =>
    setCollapsedSections((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const tocSectionIds = tocTree.tree.filter((n) => n.children.length > 0).map((n) => n.item.id);
  const tocAllCollapsed = tocSectionIds.length > 0 && tocSectionIds.every((id) => collapsedSections.has(id));
  // 默认折叠：首次构建出目录树时，把所有带子条目的章节收起（用户后续手动操作不受影响）。
  const tocCollapseInited = useRef(false);
  useEffect(() => {
    if (tocCollapseInited.current || tocSectionIds.length === 0) return;
    tocCollapseInited.current = true;
    setCollapsedSections(new Set(tocSectionIds));
  }, [tocSectionIds]);
  // scroll-spy 命中折叠章节内的条目时，自动展开该章节（保证高亮可见）。
  useEffect(() => {
    if (!activeId) return;
    const parent = tocTree.childToParent[activeId];
    if (!parent) return;
    setCollapsedSections((prev) => {
      if (!prev.has(parent)) return prev;
      const next = new Set(prev);
      next.delete(parent);
      return next;
    });
  }, [activeId, tocTree.childToParent]);

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
                      onClick={(e) => scrollToId(e, r.vulnIds[0])}
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
          <nav data-testid="toc" aria-label={t("markdown.tocAria")} className="sticky top-20 self-start">
            <div className="mb-2 flex items-center justify-between gap-2 px-2">
              <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                {t("markdown.toc")}
              </span>
              {tocSectionIds.length > 0 && (
                <button
                  type="button"
                  data-testid="toc-toggle-all"
                  onClick={() => setCollapsedSections(tocAllCollapsed ? new Set() : new Set(tocSectionIds))}
                  className="rounded px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground"
                >
                  {tocAllCollapsed ? t("markdown.expandAll") : t("markdown.collapseAll")}
                </button>
              )}
            </div>
            {collapseAllCardsBtn && <div className="mb-2 px-2">{collapseAllCardsBtn}</div>}
            <ul className="max-h-[calc(100vh-3rem)] space-y-0.5 overflow-y-auto pr-1">
              {tocTree.tree.map((node) => {
                const hasKids = node.children.length > 0;
                const collapsed = collapsedSections.has(node.item.id);
                const active = node.item.id === activeId;
                return (
                  <li key={node.item.id}>
                    <div className="flex items-center gap-0.5">
                      {hasKids ? (
                        <button
                          type="button"
                          data-testid="toc-toggle"
                          onClick={() => toggleSection(node.item.id)}
                          aria-expanded={!collapsed}
                          aria-label={collapsed ? t("markdown.expand") : t("markdown.collapse")}
                          className="flex size-4 shrink-0 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground"
                        >
                          <ChevronDown
                            className={`size-3 transition-transform duration-150 ${collapsed ? "-rotate-90" : ""}`}
                            aria-hidden="true"
                          />
                        </button>
                      ) : (
                        <span className="size-4 shrink-0" aria-hidden="true" />
                      )}
                      <a
                        href={`#${node.item.id}`}
                        onClick={(e) => scrollToId(e, node.item.id)}
                        className={`flex flex-1 items-center rounded-md px-1.5 py-1.5 text-[13px] transition-colors ${
                          node.item.level === 1 ? "font-semibold" : ""
                        } ${
                          active
                            ? "bg-accent text-foreground"
                            : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                        }`}
                      >
                        <span className="truncate">{node.item.text}</span>
                      </a>
                    </div>
                    {/* 树形缩进：竖线(ml-6=24px)对齐父标题「文本」起始列（箭头16+gap2+a-pad6=24px），
                        子条目文本落在 39px，明显缩进到父标题右侧——根治「小标题比大标题靠前」。
                        父标题前导圆点已去（active 靠 bg-accent 背景足矣）：它曾把父文本推到 36px、
                        且逼竖线落在父文本左侧，视觉上子条目反显靠前。改父前导（箭头/pad）时复核 ml-6。 */}
                    {hasKids && !collapsed && (
                      <ul
                        data-testid="toc-children"
                        className="ml-6 mt-0.5 space-y-0.5 border-l border-border/40 pl-2"
                      >
                        {node.children.map((child) => {
                          const cActive = child.id === activeId;
                          return (
                            <li key={child.id}>
                              <a
                                href={`#${child.id}`}
                                onClick={(e) => scrollToId(e, child.id)}
                                className={`group flex items-center rounded-md px-1.5 py-1 font-mono text-[11px] transition-colors ${
                                  cActive
                                    ? "bg-accent text-foreground"
                                    : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                                }`}
                              >
                                <span className="truncate">{child.text}</span>
                              </a>
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
          </nav>
        )}
        <div ref={contentRef} className="space-y-5">
          {!twoCol && collapseAllCardsBtn && (
            <div className="mb-2 flex items-center justify-between gap-2 px-0.5 py-1">
              <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                {t("markdown.findings")}
              </span>
              {collapseAllCardsBtn}
            </div>
          )}
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
              <div key={i} data-testid="vuln-grid" className="space-y-4">
                {g.blocks.map((block) => {
                  const sev = inferSeverity(block, topRiskIds);
                  // body = 原始 markdown 去掉首行标题（ID 在 header 里显示），完整保留所有字段/代码/散文——不裁剪丢信息。
                  const bodyMd = block.raw.split(/\r?\n/).slice(1).join("\n").trim();
                  const pocMd = pocById.get(block.id);
                  const subtitle = block.title || vulnPreview(block);
                  return (
                    <section
                      key={block.id}
                      id={block.id}
                      data-testid="vuln-card"
                      data-severity={sev}
                      className="vuln-entry scroll-mt-20 rounded-md border border-border bg-card p-4 shadow-[var(--shadow-card)]"
                    >
                      {/* 常驻 header：整行可点折叠（accordion）。ID + severity 药丸（暖色 --c-*）+ 一行「是什么」+ chevron。
                          min-w-0 让 subtitle truncate 生效；flex-wrap 窄屏优雅换行；折叠态也能扫。 */}
                      <button
                        type="button"
                        data-testid="vuln-toggle"
                        onClick={() => toggleCard(block.id)}
                        aria-expanded={!collapsedIds.has(block.id)}
                        aria-controls={`${block.id}-body`}
                        className="flex w-full min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1.5 text-left"
                      >
                        <span className="shrink-0 font-mono text-[13px] font-semibold text-foreground">{block.id}</span>
                        <span
                          className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide ${SEV_PILL[sev]}`}
                          data-testid="vuln-sev"
                        >
                          <span className={`size-1.5 rounded-full ${SEV_DOT[sev]}`} aria-hidden="true" data-testid="vuln-dot" />
                          {t(`vuln.severity.${sev}`, { defaultValue: sev })}
                        </span>
                        {subtitle && (
                          <span className="min-w-0 truncate text-[13px] font-medium text-foreground/70">{subtitle}</span>
                        )}
                        <ChevronDown
                          className={`ml-auto size-4 shrink-0 text-muted-foreground transition-transform duration-150 ${collapsedIds.has(block.id) ? "-rotate-90" : ""}`}
                          aria-hidden="true"
                        />
                      </button>
                      {/* body：完整原始内容（命中时并入对应 PoC），默认展开（不丢信息）；
                          本卡折叠时隐藏（扫描视图）。break-words 让长路径自动折行，不溢出卡片。 */}
                      {!collapsedIds.has(block.id) && (bodyMd || pocMd) && (
                        <div className="mt-3 space-y-3">
                          {bodyMd && (
                            <div id={`${block.id}-body`} className="prose prose-sm max-w-none break-words prose-headings:font-sans">
                              <ReactMarkdown
                                remarkPlugins={REMARK_PLUGINS}
                                rehypePlugins={[rehypeHighlight] as never}
                                components={proseComponents as never}
                              >
                                {bodyMd}
                              </ReactMarkdown>
                            </div>
                          )}
                          {pocMd && (
                            <div data-testid="vuln-poc" className="border-t border-border pt-3">
                              <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                                {t("markdown.pocSection")}
                              </div>
                              <div className="prose prose-sm max-w-none break-words prose-headings:font-sans">
                                <ReactMarkdown
                                  remarkPlugins={REMARK_PLUGINS}
                                  rehypePlugins={[rehypeHighlight] as never}
                                  components={proseComponents as never}
                                >
                                  {pocMd}
                                </ReactMarkdown>
                              </div>
                            </div>
                          )}
                        </div>
                      )}
                    </section>
                  );
                })}
              </div>
            ),
          )}
          {orphanPocEntries.length > 0 && (
            <div data-testid="poc-orphan-group" className="space-y-4">
              {orphanPocEntries.map((e) => (
                <section
                  key={e.id}
                  data-testid="poc-orphan"
                  className="vuln-entry scroll-mt-20 rounded-md border border-border bg-card p-4 shadow-[var(--shadow-card)]"
                >
                  <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                    {t("markdown.pocSection")} · {e.id}
                  </div>
                  <div className="prose prose-sm max-w-none break-words prose-headings:font-sans">
                    <ReactMarkdown
                      remarkPlugins={REMARK_PLUGINS}
                      rehypePlugins={[rehypeHighlight] as never}
                      components={proseComponents as never}
                    >
                      {e.md}
                    </ReactMarkdown>
                  </div>
                </section>
              ))}
            </div>
          )}
          {attackChainSplit && (
            <AttackChainSection md={attackChainSplit.sectionMd} count={attackChainSplit.count} />
          )}
        </div>
      </div>
    </div>
  );
}
