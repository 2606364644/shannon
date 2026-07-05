import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const css = readFileSync(resolve(__dirname, "report.css"), "utf8");

describe("report.css", () => {
  it("hljs token 配色接 DSF --c- 语义色（非硬编码 hex）", () => {
    expect(css).toContain(".hljs-keyword");
    expect(css).toContain(".hljs-string");
    expect(css).toContain(".hljs-comment");
    expect(css).toContain(".hljs-number");
    // 至少一处引用 --c- 语义色 token
    expect(css).toMatch(/var\(--c-(cyan|magenta|green|red|yellow)\)/);
  });

  it("浅色主题覆盖（.light 下有 hljs 规则）", () => {
    expect(css).toMatch(/\.light\s+\.hljs/);
  });

  it("表格 + scroll-margin + pre 样式存在", () => {
    expect(css).toContain(".prose table");
    expect(css).toContain("scroll-margin-top");
    expect(css).toContain(".prose pre");
  });
});
