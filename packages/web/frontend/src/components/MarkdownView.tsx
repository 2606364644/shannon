import { useMemo, useState, Children, type ReactNode, type ReactElement } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeSlug from "rehype-slug";
import rehypeAutolinkHeadings from "rehype-autolink-headings";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/button";
import { ChevronDown, ChevronRight } from "lucide-react";
import { MarkdownVulnCard } from "./MarkdownVulnCard";
import { splitByVulnBlocks, inferSeverity } from "@/lib/vuln-block";

interface Heading {
  id: string;
  text: string;
  level: 1 | 2 | 3;
}

interface TopRisk {
  text: string;
  vulnIds: string[];
}

/** 从 md 提取 TOC headings + 执行摘要「最高风险发现」编号条目（提取括号内 vuln ID，如 INJ-01）。 */
function parseStructure(md: string): {
  headings: Heading[];
  topRisks: TopRisk[];
} {
  const headings: Heading[] = [];
  const topRisks: TopRisk[] = [];
  const lines = md.split(/\r?\n/);
  let inExecSummary = false;
  let inNumberedList = false;
  for (const line of lines) {
    const hm = /^(#{1,3})\s+(.+)$/.exec(line);
    if (hm) {
      const level = hm[1].length as 1 | 2 | 3;
      const text = hm[2].trim();
      const id = text
        .toLowerCase()
        .replace(/[^\p{L}\p{N}]+/gu, "-")
        .replace(/^-|-$/g, "");
      headings.push({ id, text, level });
      inExecSummary = text.includes("执行摘要");
      inNumberedList = false;
      continue;
    }
    if (inExecSummary) {
      const nm = /^\d+\.\s+(.+)$/.exec(line.trim());
      if (nm) {
        inNumberedList = true;
        const text = nm[1].replace(/\*\*/g, "");
        const vulnIds = Array.from(text.matchAll(/[A-Z]+-\d+/g)).map((m) => m[0]);
        topRisks.push({ text, vulnIds });
      } else if (inNumberedList && line.trim() && !/^\d+\./.test(line.trim())) {
        inNumberedList = false;
      }
    }
  }
  return { headings, topRisks };
}

/** 拍平 ReactNode 到纯文本（用于键值检测）。 */
function flatten(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flatten).join("");
  const el = node as ReactElement<{ children?: ReactNode }>;
  if (el?.props?.children) return flatten(el.props.children);
  return "";
}

/** prose 段共享的 react-markdown 组件覆写（kv-row li / inline code / pre 复制按钮）。 */
const PROSE_COMPONENTS = {
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
          复制
        </Button>
        {children}
      </pre>
    );
  },
};

const REMARK_PLUGINS = [remarkGfm];
const REHYPE_PLUGINS = [
  rehypeSlug,
  [rehypeAutolinkHeadings, { behavior: "wrap" }],
  rehypeHighlight,
];

export function MarkdownView({ markdown }: { markdown: string }) {
  const [heroCollapsed, setHeroCollapsed] = useState(false);
  const { headings, topRisks } = useMemo(() => parseStructure(markdown), [markdown]);
  const execH2 = headings.find((h) => h.text.includes("执行摘要"));
  const showHero = !!execH2 && topRisks.length > 0;
  const topRiskIds = useMemo(() => {
    const s = new Set<string>();
    for (const r of topRisks) for (const id of r.vulnIds) s.add(id);
    return s;
  }, [topRisks]);
  const segments = useMemo(() => splitByVulnBlocks(markdown), [markdown]);

  return (
    <div className="space-y-4">
      {showHero && (
        <div
          data-testid="exec-summary-hero"
          className="rounded-md border border-border bg-card p-4"
        >
          <div className="mb-2 flex items-center justify-between font-serif text-base">
            <span>最高风险发现（按业务影响排序）</span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setHeroCollapsed((c) => !c)}
              aria-label="toggle hero"
            >
              {heroCollapsed ? <ChevronRight className="size-4" /> : <ChevronDown className="size-4" />}
              <span className="sr-only">{heroCollapsed ? "展开" : "折叠"}</span>
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

      {(() => {
        const tocItems = headings.filter((h) => h.level >= 2);
        const gridCls = tocItems.length > 0 ? "grid grid-cols-[220px_1fr] gap-6" : "grid grid-cols-1";
        return (
          <div className={gridCls}>
            {tocItems.length > 0 && (
              <nav data-testid="toc" className="sticky top-4 space-y-1 text-sm">
                {tocItems.map((h, i) => (
                  <a
                    key={`${i}-${h.id}`}
                    href={`#${h.id}`}
                    className={`block text-muted-foreground hover:text-primary ${
                      h.level === 3 ? "pl-3 text-xs" : ""
                    }`}
                  >
                    {h.text}
                  </a>
                ))}
              </nav>
            )}
            <div className="space-y-4">
              {segments.map((seg, i) =>
                seg.type === "prose" ? (
                  <div
                    key={i}
                    className="prose prose-sm max-w-none font-sans prose-headings:font-serif"
                  >
                    <ReactMarkdown
                      remarkPlugins={REMARK_PLUGINS}
                      rehypePlugins={REHYPE_PLUGINS as never}
                      components={PROSE_COMPONENTS as never}
                    >
                      {seg.md}
                    </ReactMarkdown>
                  </div>
                ) : (
                  <MarkdownVulnCard
                    key={i}
                    block={seg.block}
                    severity={inferSeverity(seg.block, topRiskIds)}
                  />
                ),
              )}
            </div>
          </div>
        );
      })()}
    </div>
  );
}
