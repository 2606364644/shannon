import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { SEV_CAP, SEV_PILL, SEV_DOT, SEV_EDGE } from "./severity-visual";

const tokensCss = readFileSync(resolve(__dirname, "../styles/tokens.css"), "utf8");

/** 形状通道单源（spec 2026-08-27 §2.2-§2.4）：sev-dot 填充比例 + SEV_EDGE 线型阶梯。
 *  色相走 pill 的 text-*（dot 用 currentColor 继承），形状/线型跨 13 主题等强可读。 */

describe("SEV_CAP：小写 severity → 展示档位（QuickReferenceTable 等小写键消费方归一）", () => {
  it("四档映射齐", () => {
    expect(SEV_CAP.critical).toBe("Critical");
    expect(SEV_CAP.high).toBe("High");
    expect(SEV_CAP.medium).toBe("Medium");
    expect(SEV_CAP.low).toBe("Low");
  });
});

describe("SEV_PILL：药丸配色（tint 底=hue 通道；文本=sev-text-* 文本步，spec §4）", () => {
  it("Critical 红 / High 橙 / Medium 黄 / Low 中性", () => {
    expect(SEV_PILL.Critical).toBe("bg-red/15 sev-text-red");
    expect(SEV_PILL.High).toBe("bg-orange/15 sev-text-orange");
    expect(SEV_PILL.Medium).toBe("bg-yellow/15 sev-text-yellow");
    expect(SEV_PILL.Low).toBe("bg-muted text-muted-foreground");
  });
});

describe("SEV_DOT：填充比例阶梯（形状通道，spec §2.2 签名）", () => {
  it("四档映射到对应填充比例 class，基类 sev-dot 由消费方拼接", () => {
    expect(SEV_DOT.Critical).toBe("sev-dot-critical");
    expect(SEV_DOT.High).toBe("sev-dot-high");
    expect(SEV_DOT.Medium).toBe("sev-dot-medium");
    expect(SEV_DOT.Low).toBe("sev-dot-low");
  });

  it("dot 类不含 bg-*（色相经 currentColor 继承自 pill 文本色，不双写）", () => {
    for (const cls of Object.values(SEV_DOT)) {
      expect(cls).not.toMatch(/bg-/);
      expect(cls).not.toMatch(/text-/);
    }
  });
});

describe("tokens.css：.sev-dot 形状类落地（conic-gradient 填充比例）", () => {
  it("含四档填充比例规则：low 空环 / medium 半 / high 3/4 / critical 满填充", () => {
    expect(tokensCss).toContain(".sev-dot-critical");
    expect(tokensCss).toContain(".sev-dot-high");
    expect(tokensCss).toContain(".sev-dot-medium");
    expect(tokensCss).toContain(".sev-dot-low");
    // 填充走 currentColor（conic-gradient），近单色主题形状仍分级
    expect(tokensCss).toMatch(/\.sev-dot-high\s*\{[^}]*conic-gradient\(currentColor[^)]*\)/);
    expect(tokensCss).toMatch(/\.sev-dot-low\s*\{[^}]*box-shadow:\s*inset/);
  });
});

describe("SEV_EDGE：线型阶梯（spec §2.3 第四通道）", () => {
  it("Critical/High 实线；Medium 虚线；Low 点线", () => {
    expect(SEV_EDGE.Critical).toBe("border-l-2 border-l-red/70");
    expect(SEV_EDGE.High).toBe("border-l-2 border-l-orange/70");
    expect(SEV_EDGE.Medium).toBe("border-l-2 border-l-yellow/70 [border-left-style:dashed]");
    expect(SEV_EDGE.Low).toBe("border-l-2 border-l-muted-foreground/40 [border-left-style:dotted]");
  });
});
