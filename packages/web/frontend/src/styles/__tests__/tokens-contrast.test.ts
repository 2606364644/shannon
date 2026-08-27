import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/** severity 色 AA 对比度验证器（spec 2026-08-27 警报语义层 §4）。
 *
 * tokens.css 曾以「AA 校验以 dev 预览页为准」人工口径——本测试把 Radix Colors
 * 的「步阶映射 UI 角色」方法论自动化：severity 文本色（--c-red/orange/yellow）
 * 是 10-11px 小文本，两种实际底色都必须 ≥ 4.5:1：
 *   ① 卡底：text-{sev} 出现在 bg-card 上（左缘/标题内联等）；
 *   ② 药丸混合底：bg-{sev}/15 pill 上的 text-{sev}（alpha 合成后的实际底色）。
 * alpha 卡面（arc）先与 --background 合成再算。失败主题在 hue 锁定（5/24/38）
 * 纪律内只调 lightness/sat 修值——「选对步位」而非「手算」。 */

/** 剥注释后解析（注释里的 `{sev}` 等花括号会截断 [^}]* 块匹配） */
const css = readFileSync(resolve(__dirname, "../../styles/tokens.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

type RGB = [number, number, number];
type HSLC = { h: number; s: number; l: number; a: number };

/** "60 3% 15%" / "240 10% 12% / 0.6" → HSLC；不匹配（hex 等）→ null */
function parseHsl(value: string): HSLC | null {
  const m = /([\d.]+)\s+([\d.]+)%\s+([\d.]+)%(?:\s*\/\s*([\d.]+))?/.exec(value);
  if (!m) return null;
  return { h: +m[1], s: +m[2], l: +m[3], a: m[4] !== undefined ? +m[4] : 1 };
}

/** HSL(0-360, 0-1, 0-1, alpha) → sRGB 0-1（CSS color() 级近似，验证用途足够） */
function hslToRgb({ h, s, l }: HSLC): RGB {
  const H = ((h % 360) + 360) % 360;
  const c = (s / 100) * (1 - Math.abs((2 * l) / 100 - 1));
  const x = c * (1 - Math.abs(((H / 60) % 2) - 1));
  const m = l / 100 - c / 2;
  const seg =
    H < 60 ? [c, x, 0] : H < 120 ? [x, c, 0] : H < 180 ? [0, c, x] :
    H < 240 ? [0, x, c] : H < 300 ? [x, 0, c] : [c, 0, x];
  return seg.map((v) => v + m) as RGB;
}

/** gamma 空间 alpha 合成（浏览器对不透明底的实际行为） */
function composite(fg: RGB, bg: RGB, alpha: number): RGB {
  return fg.map((v, i) => v * alpha + bg[i] * (1 - alpha)) as RGB;
}

function luminance([r, g, b]: RGB): number {
  const lin = (v: number) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

function contrast(a: RGB, b: RGB): number {
  const [la, lb] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (la + 0.05) / (lb + 0.05);
}

function vars(block: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of block.matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}

interface ThemeCase {
  name: string;
  card: RGB; // 有效卡底（alpha 已与 background 合成）
  sev: Record<"red" | "orange" | "yellow", RGB>; // 文本步生效色（AA 验证对象）
  tint: Record<"red" | "orange" | "yellow", RGB>; // 原色（pill tint 底用）
}

function themeCases(): ThemeCase[] {
  const cases: ThemeCase[] = [];
  const blocks: Array<[string, "dark" | "light", string]> = [];
  const root = /:root\s*\{([^}]*)\}/.exec(css);
  if (root) blocks.push(["charcoal·深基准", "dark", root[1]]);
  const light = /^\.light\s*\{([^}]*)\}/m.exec(css);
  if (light) blocks.push(["warm-paper·浅基准", "light", light[1]]);
  for (const m of css.matchAll(/\.(dark|light)\.theme-([\w-]+)\s*\{([^}]*)\}/g)) {
    blocks.push([m[2], m[1] as "dark" | "light", m[3]]);
  }
  for (const [name, mode, block] of blocks) {
    const v = vars(block);
    const card = parseHsl(v["card"] ?? "");
    const background = parseHsl(v["background"] ?? "");
    if (!card || !background) continue;
    const cardRgb = card.a < 1 ? composite(hslToRgb(card), hslToRgb(background), card.a) : hslToRgb(card);
    const sev = {} as ThemeCase["sev"];
    const tint = {} as ThemeCase["tint"];
    for (const key of ["red", "orange", "yellow"] as const) {
      const parsed = parseHsl(v[`c-${key}`] ?? "");
      if (parsed) {
        tint[key] = hslToRgb(parsed);
        sev[key] = effectiveText(tint[key], mode);
      }
    }
    if (Object.keys(sev).length === 3) cases.push({ name, card: cardRgb, sev, tint });
  }
  return cases;
}

const PILL_ALPHA = 0.15; // bg-{sev}/15

/** 文本步（spec §4 Radix 步阶落地）：severity 文本色 = --c-{sev} 向 --sev-text-toward
 *  混 --sev-text-strength（深色主题向白提亮、浅色主题向黑压深），tint 底用原色。
 *  模式基础值定义在 :root / .light，扩展主题块继承（解析时按模式回落）。 */
function textStepProps(mode: "dark" | "light"): { strength: number; toward: RGB } {
  const base =
    (mode === "dark" ? /:root\s*\{([^}]*)\}/.exec(css)?.[1] : /^\.light\s*\{([^}]*)\}/m.exec(css)?.[1]) ?? "";
  const v = vars(base);
  const strength = parseFloat(v["sev-text-strength"] ?? "") / 100;
  const towardRaw = v["sev-text-toward"] ?? "";
  const toward: RGB =
    towardRaw.trim() === "white" ? [1, 1, 1] : towardRaw.trim() === "black" ? [0, 0, 0] : [0, 0, 0];
  return { strength, toward };
}

/** 文本步生效色：raw 向 toward 混（1-strength 比例）；strength 缺失（NaN）→ 原色 */
function effectiveText(raw: RGB, mode: "dark" | "light"): RGB {
  const { strength, toward } = textStepProps(mode);
  if (!Number.isFinite(strength)) return raw;
  return raw.map((c, i) => c * strength + toward[i] * (1 - strength)) as RGB;
}

describe("severity AA 对比度 × 全主题（spec 2026-08-27 §4 自动化验证器）", () => {
  const cases = themeCases();

  it("解析到全部 13 个主题（2 基准 + 11 扩展）——解析器失效即报警", () => {
    expect(cases.length).toBe(13);
  });

  it("文本步 token 双模式齐备（--sev-text-strength / --sev-text-toward）", () => {
    for (const mode of ["dark", "light"] as const) {
      const { strength } = textStepProps(mode);
      expect(Number.isFinite(strength)).toBe(true);
    }
  });

  it.each(
    cases.flatMap((c) =>
      (["red", "orange", "yellow"] as const).map((k) => ({
        theme: c.name,
        color: k,
        ratio: contrast(c.sev[k], c.card),
      })),
    ),
  )("卡底 $theme · $color 对比度 ≥ 4.5（实测 $ratio）", ({ ratio }) => {
    expect(ratio).toBeGreaterThanOrEqual(4.5);
  });

  it.each(
    cases.flatMap((c) =>
      (["red", "orange", "yellow"] as const).map((k) => ({
        theme: c.name,
        color: k,
        ratio: contrast(c.sev[k], composite(c.tint[k], c.card, PILL_ALPHA)),
      })),
    ),
  )("药丸混合底 $theme · $color 对比度 ≥ 4.5（实测 $ratio）", ({ ratio }) => {
    expect(ratio).toBeGreaterThanOrEqual(4.5);
  });
});
