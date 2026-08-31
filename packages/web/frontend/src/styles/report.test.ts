import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const css = readFileSync(resolve(__dirname, "report.css"), "utf8");

describe("report.css", () => {
  it("hljs token 配色接代码主题 --code-hl-* token（非硬编码 hex、非 web 主题 --c- 语义色）", () => {
    expect(css).toContain(".hljs-keyword");
    expect(css).toContain(".hljs-string");
    expect(css).toContain(".hljs-comment");
    expect(css).toContain(".hljs-number");
    // syntax 色引用代码主题 token（面板永远深色 → 一套配色全局生效）
    expect(css).toMatch(/var\(--code-hl-(keyword|string|number|title|meta)\)/);
    // 代码主题与 web 设计主题解耦：hljs 规则不再消费 --c- 语义色（随主题漂移）
    const hljsBlocks = css.match(/\.hljs-[\w.,\s]+?\{[^}]*\}/g) ?? [];
    for (const block of hljsBlocks) {
      expect(block, `hljs 规则不得引用 web 主题语义色：${block.slice(0, 60)}`).not.toMatch(/--c-/);
    }
  });

  it("代码主题全局唯一：无 .light hljs 覆盖（面板不分模式）", () => {
    expect(css).not.toMatch(/\.light\s+\.hljs/);
  });

  it("表格 + scroll-margin + pre 样式存在", () => {
    expect(css).toContain(".prose table");
    expect(css).toContain("scroll-margin-top");
    expect(css).toContain(".prose pre");
  });

  it("终端证据窗：pre/代码面板用代码主题底 --code-bg（非设计主题 --muted）", () => {
    expect(css).toMatch(/\.prose pre,\s*\n\.code-panel\s*\{[^}]*var\(--code-bg\)/);
    // 旧「pre 接 --muted」是代码块与正文分不开的根因，锁定不再回归
    expect(css).not.toMatch(/\.prose pre\s*\{[^}]*var\(--muted\)/);
    // 结构化路径（VulnerabilityCard）与 md 路径同一面板材质 + 实测输出绿语义变体
    expect(css).toContain(".code-panel-ok");
    // 打印浅色化兜底：print 块内面板退浅底深字（浏览器默认不打印背景，
    // 深底浅字会变白纸白字），不得沿用深色 --code-bg
    const printBlock = /@media print\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? "";
    expect(printBlock).toMatch(/\.prose pre,/);
    expect(printBlock).not.toMatch(/var\(--code-bg\)/);
  });
});
