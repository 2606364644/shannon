import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const textareaVariants = cva(
  "flex min-h-[60px] w-full rounded-md border border-input bg-transparent px-3 py-2 shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
  {
    variants: {
      size: {
        // 默认（shadcn 惯例）：移动端 text-base(16px，防 iOS 聚焦缩放) / 桌面 md:text-sm(14px)。
        default: "text-base md:text-sm",
        // 紧凑：密集表单用——全断点 text-xs(12px)，显式压掉 default 的 md:text-sm 防桌面端漏回 14px。
        sm: "text-xs md:text-xs",
      },
    },
    defaultVariants: {
      size: "default",
    },
  }
)

export interface TextareaProps
  extends React.ComponentProps<"textarea">,
    VariantProps<typeof textareaVariants> {}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, size, ...props }, ref) => {
    return (
      <textarea
        className={cn(textareaVariants({ size, className }))}
        ref={ref}
        {...props}
      />
    )
  }
)
Textarea.displayName = "Textarea"

export { Textarea, textareaVariants }
