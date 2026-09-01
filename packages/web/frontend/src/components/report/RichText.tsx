import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { HIGHLIGHT_PLUGIN } from "@/lib/hljs-langs";
import { copyableMarkdownCodeComponents } from "@/components/markdown/CopyableMarkdownCode";

/**
 * 结构化报告（report_data.json）的 md 文本字段渲染——narrative 三段 / 摘要叙事 /
 * 攻击链叙事。纯渲染：不做漏洞结构解析、不做 severity 推断（spec 2026-08-26 §7.2
 * 报告页删解析层）；报告结构由 report_data.json 承载，这里只把已是 md 的文本字段
 * 铺出来。轻量于 MarkdownView（无 TOC / 无卡片切分——那是 md 降级路径的职责）。
 *
 * 代码块挂语法高亮 + 统一复制按钮（与 md 降级路径同源同语言子集）：修复建议里
 * 的代码示例 / 叙事里的命令不再单色，且所有 fenced code 均可一键复制。
 *
 * 行宽由页面版心（ReportTab REPORT_COL_CLS）统一守住——组件自身 max-w-none 不做
 * 二次护栏（2026-08-26 满宽实验曾在组件内加 max-w-3xl 护栏，与满宽卡片边框形成
 * 768px vs 满宽的双重宽度 → 左重右空，已随版心恢复回滚）。
 */
export function RichText({ text, className }: { text: string; className?: string }) {
  return (
    <div
      className={`prose prose-sm max-w-none break-words prose-headings:font-sans ${className ?? ""}`}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[...HIGHLIGHT_PLUGIN] as never}
        components={copyableMarkdownCodeComponents}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
