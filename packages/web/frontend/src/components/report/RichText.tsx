import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * 结构化报告（report_data.json）的 md 文本字段渲染——narrative 三段 / 摘要叙事 /
 * 攻击链叙事。纯渲染：不做漏洞结构解析、不做 severity 推断（spec 2026-08-26 §7.2
 * 报告页删解析层）；报告结构由 report_data.json 承载，这里只把已是 md 的文本字段
 * 铺出来。轻量于 MarkdownView（无 TOC / 无卡片切分——那是 md 降级路径的职责）。
 *
 * 外层 max-w-3xl（768px）是满宽报告页（2026-08-26 放宽）的行宽护栏所在层：页面级
 * 不再收窄，叙述散文的可读行宽在这里守住（中文 ~45 字/行）；窄于 768px 的容器
 * （如 narrative 三段列）不受影响。需要满宽的调用点经 className 覆盖。
 */
export function RichText({ text, className }: { text: string; className?: string }) {
  return (
    <div
      className={`max-w-3xl prose prose-sm break-words prose-headings:font-sans ${className ?? ""}`}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}
