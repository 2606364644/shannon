import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeftRight, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface Props {
  base: string;
  head: string;
  onBase: (v: string) => void;
  onHead: (v: string) => void;
  /** 校验错误（就近显示在区间控件内；null=无错）。 */
  error?: string | null;
  /** 回填确认信号（时间戳）：变化时容器做一次 coral 环脉冲——链接解析自动回填
   *  refs 后的「答案式」动效（prefers-reduced-motion / 不支持 WAAPI 时静默跳过）。 */
  flashAt?: number;
}

/** MR 变更范围区间控件（2026-09-04 MR 表单重排）：base ⟷ head 做成一个视觉整体
 *  ——圆角容器内两个 mono 输入位 + 中间 swap 按钮 + 就绪摘要 `base..head`（git
 *  range 语法，就地确认将检测什么）。swap 是 base/head 填反（git 用户高频手滑）
 *  的一键救回；窄屏纵排时连接钮旋转 90° 表达上下交换。 */
export function RefRangeInput({ base, head, onBase, onHead, error, flashAt }: Props) {
  const { t } = useTranslation();
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!flashAt || !boxRef.current) return;
    if (typeof boxRef.current.animate !== "function") return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    boxRef.current.animate(
      [
        { boxShadow: "0 0 0 0 hsl(var(--primary) / 0)" },
        { boxShadow: "0 0 0 4px hsl(var(--primary) / 0.35)" },
        { boxShadow: "0 0 0 0 hsl(var(--primary) / 0)" },
      ],
      { duration: 700, easing: "ease-out" },
    );
  }, [flashAt]);

  const swap = () => { onBase(head); onHead(base); };
  const ready = !!(base.trim() && head.trim());

  return (
    <div ref={boxRef} className="space-y-2.5 rounded-lg border border-border bg-secondary/40 p-3">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-[1fr_auto_1fr]">
        <div className="space-y-1.5">
          <Label className="text-xs font-medium" htmlFor="mr-base-ref">{t("scan.mr.baseLabel")}</Label>
          <Input
            id="mr-base-ref"
            data-testid="mr-base-ref"
            value={base}
            onChange={(e) => onBase(e.target.value)}
            placeholder="main"
            size="sm"
            className="font-mono"
          />
        </div>
        <div className="flex items-end justify-center pb-0.5">
          <Button
            type="button"
            variant="outline"
            size="icon-sm"
            data-testid="mr-ref-swap"
            aria-label={t("scan.mr.swap")}
            title={t("scan.mr.swap")}
            onClick={swap}
            className="rotate-90 sm:rotate-0"
          >
            <ArrowLeftRight />
          </Button>
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs font-medium" htmlFor="mr-head-ref">{t("scan.mr.headLabel")}</Label>
          <Input
            id="mr-head-ref"
            data-testid="mr-head-ref"
            value={head}
            onChange={(e) => onHead(e.target.value)}
            placeholder="feature/branch"
            size="sm"
            className="font-mono"
          />
        </div>
      </div>
      {ready ? (
        <div data-testid="mr-range-summary" className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
          <Check className="size-3.5 shrink-0 text-green" aria-hidden="true" />
          <code className="font-mono text-[11px] text-foreground">{base.trim()}..{head.trim()}</code>
          <span className="text-[11px] text-muted-foreground">{t("scan.mr.rangeReady")}</span>
        </div>
      ) : error ? (
        <div className="text-xs text-destructive">{error}</div>
      ) : null}
    </div>
  );
}
