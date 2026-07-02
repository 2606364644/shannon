import { useMemo, useState, type ReactNode, type ReactElement } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeSlug from "rehype-slug";
import rehypeAutolinkHeadings from "rehype-autolink-headings";
import "../styles/markdown.css";

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

export function MarkdownView({ markdown }: { markdown: string }) {
  const [heroCollapsed, setHeroCollapsed] = useState(false);
  const { headings, topRisks } = useMemo(() => parseStructure(markdown), [markdown]);
  const execH2 = headings.find((h) => h.text.includes("执行摘要"));
  const showHero = !!execH2 && topRisks.length > 0;

  return (
    <div className="md-view">
      {showHero && (
        <div data-testid="exec-summary-hero" className="hero">
          <div className="hero-title">
            最高风险发现（按业务影响排序）
            <button onClick={() => setHeroCollapsed((c) => !c)} aria-label="toggle hero">
              {heroCollapsed ? "展开 ▸" : "折叠 ▾"}
            </button>
          </div>
          {!heroCollapsed && (
            <ol>
              {topRisks.map((r, i) => (
                <li key={i}>
                  {r.vulnIds.length > 0 && <span className="mono kv-vuln-id">{r.vulnIds.join("/")}</span>}{" "}
                  {r.text}
                </li>
              ))}
            </ol>
          )}
        </div>
      )}

      <div className="md-layout">
        <nav data-testid="toc" className="toc">
          {headings
            .filter((h) => h.level >= 2)
            .map((h, i) => (
              <a key={`${i}-${h.id}`} href={`#${h.id}`} className={`toc-l${h.level}`}>
                {h.text}
              </a>
            ))}
        </nav>
        <div className="md-body serif">
          <ReactMarkdown
            rehypePlugins={[
              rehypeSlug,
              [rehypeAutolinkHeadings, { behavior: "wrap" }],
              rehypeHighlight,
            ]}
            components={{
              // 加粗键值：`- **key:** value` → 对齐 key-value 行
              li: ({ children, ...props }) => {
                const text = flatten(children);
                const m = /^\*\*(.+?):\*\*\s*(.*)$/.exec(text);
                if (m) {
                  return (
                    <li {...props} className="kv-row">
                      <span className="kv-key mono">{m[1]}</span>
                      <span className="kv-val">{m[2]}</span>
                    </li>
                  );
                }
                return <li {...props}>{children}</li>;
              },
              // witness PoC 代码块：可复制
              code: ({ className, children, ...props }) => (
                <code {...props} className={`md-code ${className ?? ""}`}>
                  {children}
                  <button
                    className="copy-btn"
                    onClick={(e) => {
                      navigator.clipboard?.writeText(String(children));
                      e.currentTarget.textContent = "✓";
                    }}
                  >
                    复制
                  </button>
                </code>
              ),
            }}
          >
            {markdown}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
