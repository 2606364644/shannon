import { describe, it, expect } from "vitest";
import zh from "../locales/zh.json";
import en from "../locales/en.json";

type Obj = Record<string, unknown>;
function leafKeys(obj: Obj, prefix = ""): string[] {
  return Object.entries(obj).flatMap(([k, v]) =>
    v && typeof v === "object" ? leafKeys(v as Obj, `${prefix}${k}.`) : [`${prefix}${k}`]
  );
}
function get(obj: Obj, path: string): unknown {
  return path.split(".").reduce<unknown>((acc, p) => (acc && typeof acc === "object" ? (acc as Obj)[p] : undefined), obj);
}

describe("locale 完整性", () => {
  const zhKeys = leafKeys(zh as Obj).sort();
  const enKeys = leafKeys(en as Obj).sort();

  it("zh 与 en 的 key 集合完全一致", () => {
    expect(enKeys, "en 与 zh 的 key 不一致").toEqual(zhKeys);
  });

  it("en 所有 key 都有非空值", () => {
    for (const k of enKeys) {
      expect(get(en as Obj, k), `en.${k} 缺值`).toBeTruthy();
    }
  });

  // 防回退：zh 导航项必须翻译为中文。原 bug 是 nav.dashboard/workspaces/scan/settings
  // 在 zh.json 里值直接写成了英文（key 存在故不触发 fallback，测试只校验 key 集合也漏过），
  // 表现为中文模式下导航栏仅"仓库"是中文，其余 4 项仍是英文。
  it("zh 导航项必须含汉字（防 nav 值漏翻成英文）", () => {
    const cjk = /[一-鿿]/;
    for (const k of ["dashboard", "workspaces", "repos", "scan", "settings"]) {
      const v = get(zh as Obj, `nav.${k}`);
      expect(v, `zh.nav.${k} 缺值`).toBeTruthy();
      expect(cjk.test(String(v)), `zh.nav.${k} 应含汉字，当前为 "${v}"`).toBe(true);
    }
  });
});
