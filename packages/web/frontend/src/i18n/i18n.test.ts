import { describe, it, expect, beforeEach } from "vitest";
import i18n from "@/i18n";

describe("i18n init", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  // i18next 在 init 时把字符串 fallbackLng 规范化为数组(transformOptions),
  // 故 options.fallbackLng 实际为 ["zh"]——这里断言规范化后的形态。
  it("fallbackLng 为 zh", () => {
    expect(i18n.options.fallbackLng).toEqual(["zh"]);
  });

  it("zh/en 关键 key 都有值且可切换", () => {
    expect(i18n.t("nav.repos")).toBe("仓库");
    i18n.changeLanguage("en");
    expect(i18n.t("nav.repos")).toBe("Repositories");
  });
});
