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
});
