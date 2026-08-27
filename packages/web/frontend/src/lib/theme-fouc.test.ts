import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { THEMES, defaultThemeFor, ThemeId } from "@/lib/theme";

/** index.html FOUC 预挂脚本契约（2026-08-27 锁）。
 *
 * 内联脚本持有一份主题→class 映射 + 回落默认对，与 theme.ts 双写——openai 主题
 * 入库时曾漏更 DEF 表（stored=openai 首帧错挂默认对再闪变）、默认对调整时回落值
 * 滞后，两次漂移都因无测试锁定。本测试把三件事钉死在单源 theme.ts 上：
 *   ① DEF 表覆盖全部主题 id 且 mode/paletteClass 与 THEMES 一致；
 *   ② 无 stored/非法值回落对 = defaultThemeFor（与运行时同源）；
 *   ③ legacy 旧值映射 = normalizeStored 的映射子集。 */

const html = readFileSync(resolve(__dirname, "../../index.html"), "utf8");

function defTable(): Record<string, string[]> {
  const m = /var DEF = \{([\s\S]*?)\};/.exec(html);
  if (!m) return {};
  const out: Record<string, string[]> = {};
  for (const entry of m[1].matchAll(/"?([\w-]+)"?\s*:\s*\[([^\]]*)\]/g)) {
    out[entry[1]] = entry[2].split(",").map((s) => s.trim().replace(/^"|"$/g, "")).filter(Boolean);
  }
  return out;
}

describe("index.html FOUC 脚本与 theme.ts 单源一致", () => {
  it("DEF 表覆盖全部 13 主题，mode+palette class 与 THEMES 逐项一致", () => {
    const def = defTable();
    expect(Object.keys(def).length).toBe(THEMES.length);
    for (const t of THEMES) {
      expect(def[t.id], `DEF 缺主题 ${t.id}`).toBeDefined();
      // charcoal paletteClass=null → 仅 mode；其余 mode + paletteClass
      expect(def[t.id]).toEqual(t.paletteClass ? [t.mode, t.paletteClass] : [t.mode]);
    }
  });

  it("回落默认对 = defaultThemeFor（浅色 openai / 深色 graphite，同源不漂移）", () => {
    const fallback = /\.matches \? "([\w-]+)" : "([\w-]+)"/.exec(html);
    expect(fallback, "回落三元表达式未找到").not.toBeNull();
    expect(fallback![1]).toBe(defaultThemeFor("light"));
    expect(fallback![2]).toBe(defaultThemeFor("dark"));
  });

  it("legacy 旧值映射与 normalizeStored 一致（dark→charcoal / light→warm-paper / frost→mac）", () => {
    const legacy = /var legacy = \{([^}]*)\};/.exec(html);
    expect(legacy, "legacy 映射未找到").not.toBeNull();
    const map: Record<string, string> = {};
    for (const m of legacy![1].matchAll(/([\w-]+)\s*:\s*"([\w-]+)"/g)) map[m[1]] = m[2];
    expect(map).toEqual({ dark: "charcoal", light: "warm-paper", frost: "mac" });
  });

  it("异常兜底挂默认深色主题的 mode+palette（dark + theme-graphite）", () => {
    const dark = defaultThemeFor("dark");
    const palette = THEMES.find((t) => t.id === dark)?.paletteClass ?? null;
    const catchBlock = /catch \(e\) \{([\s\S]*?)\}/.exec(html);
    expect(catchBlock, "catch 兜底未找到").not.toBeNull();
    expect(catchBlock![1]).toContain('"dark"');
    if (palette) expect(catchBlock![1]).toContain(`"${palette}"`);
  });

  it("THEMES 的 id 全集作为 ThemeId 类型健全性（openai 在列）", () => {
    const ids: ThemeId[] = THEMES.map((t) => t.id) as ThemeId[];
    expect(ids).toContain("openai");
    expect(ids).toContain("graphite");
  });
});
