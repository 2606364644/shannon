import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const inputVariants = cva(
  "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
  {
    variants: {
      size: {
        // 默认（shadcn 惯例）：移动端 text-base(16px，防 iOS 聚焦缩放) / 桌面 md:text-sm(14px)。
        default: "text-base md:text-sm",
        // 紧凑：密集表单用——全断点 text-xs(12px)。必须显式带 md:text-xs，否则 tailwind-merge
        // 会把 default 的 md:text-sm 当作独立响应式组保留，桌面端漏回 14px（与表单 11–13px 字阶格格不入）。
        sm: "text-xs md:text-xs",
      },
    },
    defaultVariants: {
      size: "default",
    },
  }
)

export interface InputProps
  extends Omit<React.ComponentProps<"input">, "size">,
    VariantProps<typeof inputVariants> {}

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, size, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(inputVariants({ size, className }))}
        ref={ref}
        {...props}
      />
    )
  }
)
Input.displayName = "Input"

export { Input, inputVariants }
