import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Copy, Check } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";

const FEEDBACK_MS = 1200;

/** 小图标复制按钮。点击写剪贴板，成功切 Check 图标 1.2s 反馈；失败 toast。
 * 可经 className 叠 absolute 定位（如来源列覆盖在长 URL 右侧）。
 * testId：结构化报告 POC「复制 curl」等测试锚点（可选）。 */
export function CopyButton({ value, className, ariaLabel, testId }: {
  value: string;
  className?: string;
  ariaLabel?: string;
  testId?: string;
}) {
  const { t } = useTranslation();
  const [done, setDone] = useState(false);
  const Icon = done ? Check : Copy;

  async function onCopy() {
    try {
      await navigator.clipboard?.writeText(value);
      setDone(true);
      setTimeout(() => setDone(false), FEEDBACK_MS);
    } catch {
      toast.error(t("common.copyFailed"));
    }
  }

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      onClick={onCopy}
      aria-label={done ? t("common.copied") : (ariaLabel ?? t("common.copy"))}
      className={className}
      data-testid={testId}
    >
      <Icon />
    </Button>
  );
}
