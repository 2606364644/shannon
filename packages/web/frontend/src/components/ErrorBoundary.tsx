import { Component, type ErrorInfo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";

interface Props {
  children: ReactNode;
}
interface State {
  hasError: boolean;
  error?: Error;
}

/**
 * 局部错误边界:捕获子树渲染异常(如某 tab 数据畸形致 Object.entries(undefined)),
 * 降级显示 fallback 而非整页白屏 —— 项目无全局 ErrorBoundary,任一子组件抛错会卸载
 * 整棵路由子树。WorkspaceDetail 以 key={当前 tab} 包裹 <Outlet/>,切 tab 自动 reset。
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[ErrorBoundary] captured render error:", error, info);
  }

  reset = (): void => {
    this.setState({ hasError: false, error: undefined });
  };

  render(): ReactNode {
    if (this.state.hasError) {
      return <ErrorFallback error={this.state.error} onReset={this.reset} />;
    }
    return this.props.children;
  }
}

function ErrorFallback({ error, onReset }: { error?: Error; onReset: () => void }) {
  const { t } = useTranslation();
  return (
    <div
      role="alert"
      className="space-y-2 rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive"
    >
      <div className="font-medium">{t("workspaceDetail.errorBoundary.title")}</div>
      <div className="text-xs opacity-80">{t("workspaceDetail.errorBoundary.hint")}</div>
      {error?.message && (
        <div className="break-all font-mono text-[0.7rem] opacity-70">{error.message}</div>
      )}
      <Button size="sm" variant="outline" onClick={onReset}>
        {t("common.retry")}
      </Button>
    </div>
  );
}
