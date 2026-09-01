import { Children, type ReactElement, type ReactNode } from "react";
import type { Components } from "react-markdown";
import { CopyButton } from "@/components/CopyButton";

/** 拍平 react-markdown / rehype-highlight 产出的 ReactNode，得到可复制源码文本。 */
function flatten(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flatten).join("");
  const el = node as ReactElement<{ children?: ReactNode }>;
  if (el?.props?.children) return flatten(el.props.children);
  return "";
}

/**
 * Markdown block-code 的共享渲染协议：
 * - `code` 保留 language-xxx class，供既有代码主题 / 语法高亮样式消费；
 * - `pre` 统一加语言角标与复制按钮；
 * - 复制按钮用 CopyButton 的受控反馈（图标 1.2s 后恢复），不再手改 DOM 文本。
 */
export const copyableMarkdownCodeComponents: Components = {
  code: ({ className, children, node: _node, ...props }) => (
    <code {...props} className={`font-mono ${className ?? ""}`}>
      {children}
    </code>
  ),
  pre: ({ children, node: _node, ...props }) => {
    const codeChild = Children.toArray(children)[0] as
      | ReactElement<{ className?: string; children?: ReactNode }>
      | undefined;
    const cls = codeChild?.props.className ?? "";
    const lang = /language-([\w-]+)/.exec(cls)?.[1] ?? "";
    const text = flatten(codeChild?.props.children);

    return (
      <pre {...props} data-testid="code-block" className="group relative pt-7">
        <div className="absolute right-1 top-1 flex items-center gap-1">
          {lang && (
            <span
              data-testid="code-lang"
              className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground/70"
            >
              {lang}
            </span>
          )}
          <CopyButton
            value={text}
            testId="copy-btn"
            className="copy-btn h-6 w-6 opacity-50 transition-opacity group-hover:opacity-100"
          />
        </div>
        {children}
      </pre>
    );
  },
};
