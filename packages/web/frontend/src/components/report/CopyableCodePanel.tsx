import type { ReactNode } from "react";
import { CopyButton } from "@/components/CopyButton";

/**
 * 结构化报告原生代码面板的统一复制入口。
 *
 * Markdown 路径由 CopyableMarkdownCodeComponents 处理；本组件覆盖
 * report_data.json 中不是 Markdown、但同样渲染为 <pre> 的源码 / 命令 / 实测输出：
 * problem-point snippet、POC、verify command、dynamic evidence 等。
 * value 必须传原始文本，不能从高亮后的 DOM 反推，避免按钮文案混进剪贴板。
 */
export function CopyableCodePanel({
  value,
  children,
  testId,
  copyTestId,
  copyLabel,
  className,
}: {
  value: string;
  children: ReactNode;
  testId?: string;
  copyTestId?: string;
  copyLabel?: string;
  className?: string;
}) {
  return (
    <pre
      data-testid={testId}
      className={`${className ?? ""} whitespace-pre-wrap pr-9`}
    >
      {children}
      <CopyButton
        value={value}
        testId={copyTestId}
        ariaLabel={copyLabel}
        className="code-chrome absolute right-1 top-1"
      />
    </pre>
  );
}
