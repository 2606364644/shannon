import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-all focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default:
          "bg-primary text-primary-foreground shadow hover:bg-primary/90",
        destructive:
          "bg-destructive text-destructive-foreground shadow-sm hover:bg-destructive/90",
        outline:
          "border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground",
        secondary:
          "bg-secondary text-secondary-foreground shadow-sm hover:bg-secondary/80",
        ghost: "hover:bg-accent hover:text-accent-foreground",
        link: "text-primary underline-offset-4 hover:underline",
        // cta · 主命令按钮：coral 实心 + sans 字体 + 柔和材质阴影 + hover 微浮。
        // 不再用 ❯ 提示符 / mono / neon 光晕（过重且与文案 + 号重复）。
        // 靠质感（阴影 + hover 浮起）区分主次，尺寸同 default(h-9)，不加大。
        // 2026-08-25 mac 质感修订：经 --radius-cta 消费胶囊几何（mac 980px）；
        // 未定义该 token 的主题回落 calc(var(--radius) - 2px) = rounded-md 等值，
        // 与 --backdrop-* 同一套「未定义即回落」idiom（tailwind 3.4 同 utility
        // 任意值输出在具名值之后，覆盖基类 rounded-md 生效）。
        cta:
          "bg-primary text-primary-foreground font-medium shadow-[var(--shadow-cta)] hover:shadow-[var(--shadow-cta-hover)] hover:-translate-y-px active:translate-y-0 transition-all [border-radius:var(--radius-cta,calc(var(--radius)_-_2px))]",
        // toolbar · 工作区页操作条按钮（切换工作区/成员/仓库/认证/HOST/置顶）：card 表面
        // 浮于页面 + hover 上浮 -2px + 暖色柔阴影 + 图标染 coral（与 cta 同一浮动语言）。
        // 图标默认 muted，hover 跟随按钮整体上浮后点亮，给出可点击反馈。
        toolbar:
          "border border-input bg-card text-foreground shadow-[var(--shadow-toolbar)] hover:-translate-y-0.5 hover:border-primary/45 hover:shadow-[var(--shadow-toolbar-hover)] active:translate-y-0 [&_svg]:text-muted-foreground [&_svg]:transition-colors hover:[&_svg]:text-primary",
      },
      size: {
        default: "h-9 px-4 py-2",
        sm: "h-8 rounded-md px-3 text-xs",
        lg: "h-10 rounded-md px-8",
        icon: "h-9 w-9",
        "icon-sm": "h-7 w-7",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

export interface ButtonProps
  extends React.ComponentProps<"button">,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

function Button({ className, variant, size, asChild = false, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : "button"
  return (
    <Comp
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button, buttonVariants }
