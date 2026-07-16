/**
 * 页面标题区 —— 统一三页（workspaces / repos / scan）的 h1 + 副标题。
 * 设计语言对齐现有页面标题：text-xl font-semibold tracking-tight + muted 副标题。
 */
interface PageHeaderProps {
  title: string;
  subtitle?: string;
}

export function PageHeader({ title, subtitle }: PageHeaderProps) {
  return (
    <div>
      <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
      {subtitle && <p className="mt-0.5 text-sm text-muted-foreground">{subtitle}</p>}
    </div>
  );
}
