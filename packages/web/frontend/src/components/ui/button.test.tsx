import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Button, buttonVariants } from "./button";

describe("Button cta 胶囊几何（2026-08-25 mac 质感修订）", () => {
  it("cta 变体经 --radius-cta 消费胶囊圆角，未定义 token 的主题回落 rounded-md 等值", () => {
    const cta = buttonVariants({ variant: "cta" });
    // 回落值 calc(var(--radius) - 2px) = tailwind rounded-md 的圆角公式；
    // tailwind 3.4 同 utility 任意值输出在具名值之后 → 覆盖基类 rounded-md 生效
    expect(cta).toContain(
      "[border-radius:var(--radius-cta,calc(var(--radius)_-_2px))]",
    );
  });

  it("其他变体不受影响（无 --radius-cta 消费）", () => {
    const plain = buttonVariants({ variant: "default" });
    expect(plain).not.toContain("--radius-cta");
  });

  it("渲染 cta 按钮带胶囊类", () => {
    render(<Button variant="cta">新建扫描</Button>);
    expect(screen.getByRole("button", { name: "新建扫描" }).className).toContain(
      "--radius-cta",
    );
  });
});
