import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const css = readFileSync(resolve(__dirname, "index.css"), "utf8");

// prose 覆盖层把 @tailwindcss/typography 的 --tw-prose-* 接到双主题 --prose-* / --foreground
// channel。漏接任何一个 → 该字段回退 typography 默认值（其默认色假定深底，浅字），
// 切 light 后浅字落在浅底上对比~1:1 看不见。此处守护已知的全部关键字段。
describe("index.css prose 覆盖层（双主题护盾）", () => {
  it("--tw-prose-pre-code 接双主题 token（不回退 typography 默认浅灰）", () => {
    // 回归 2026-07-28：漏设 --tw-prose-pre-code → 代码块普通文字（未被 hljs 着色部分）
    // 用 typography 默认 #e5e7eb 浅灰，落在 light 浅奶油 pre 底上完全看不见。
    expect(css).toContain("--tw-prose-pre-code");
    expect(css).toMatch(/--tw-prose-pre-code:\s*hsl\(var\(--foreground\)\)/);
  });

  it("prose 覆盖层接全套关键 --tw-prose-* channel（body / code / pre-bg 等）", () => {
    expect(css).toMatch(/\.prose\s*\{/);
    expect(css).toContain("--tw-prose-body");
    expect(css).toContain("--tw-prose-code");
    expect(css).toContain("--tw-prose-pre-bg");
  });
});
