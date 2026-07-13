import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Copy, Check } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

const FEEDBACK_MS = 1200;

/** 小图标复制按钮。点击写剪贴板，成功切 Check 图标 1.2s 反馈；失败 toast。
 * 可经 className 叠 absolute 定位（如来源列覆盖在长 URL 右侧）。 */
export function CopyButton({ value, className, ariaLabel }: {
  value: string;
  className?: string;
  ariaLabel?: string;
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
    <button
      type="button"
      onClick={onCopy}
      aria-label={done ? t("common.copied") : (ariaLabel ?? t("common.copy"))}
      className={cn(
        "inline-flex h-6 w-6 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground",
        className,
      )}
    >
      <Icon className="h-3.5 w-3.5" />
    </button>
  );
}
