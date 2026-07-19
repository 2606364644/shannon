/**
 * 页面标题区 —— 统一三页（workspaces / repos / scan）的 h1 + 副标题。
 * 设计语言对齐现有页面标题：text-xl font-semibold tracking-tight + muted 副标题。
 */
interface PageHeaderProps {
  title: string;
  subtitle?: string;
  /** 标题行右侧操作区（如主 CTA）；不传则不渲染，不影响其他页面 */
  action?: React.ReactNode;
}

export function PageHeader({ title, subtitle, action }: PageHeaderProps) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-0.5 text-sm text-muted-foreground">{subtitle}</p>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}
