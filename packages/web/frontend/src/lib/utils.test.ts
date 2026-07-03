import { describe, it, expect } from "vitest";
import { cn } from "@/lib/utils";

describe("cn()", () => {
  it("合并多个 class", () => {
    expect(cn("a", "b")).toBe("a b");
  });
  it("过滤 false / undefined / null", () => {
    expect(cn("a", false, undefined, null, "b")).toBe("a b");
  });
  it("tailwind-merge 解冲突（后胜）", () => {
    expect(cn("p-1", "p-2")).toBe("p-2");
  });
  it("条件对象", () => {
    expect(cn("a", { b: true, c: false })).toBe("a b");
  });
});
