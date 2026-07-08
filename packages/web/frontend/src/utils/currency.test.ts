import { describe, it, expect } from "vitest";
import { fmtCost, currencySymbol } from "./currency";

describe("fmtCost", () => {
  it("CNY → ¥", () => {
    expect(fmtCost(0.0886, "CNY")).toBe("¥0.09");
  });
  it("USD → $", () => {
    expect(fmtCost(0.0123, "USD")).toBe("$0.01");
  });
  it("null → —", () => {
    expect(fmtCost(null)).toBe("—");
  });
  it("undefined currency → $", () => {
    expect(fmtCost(1.5)).toBe("$1.50");
  });
});

describe("currencySymbol", () => {
  it("CNY → ¥", () => {
    expect(currencySymbol("CNY")).toBe("¥");
  });
  it("unknown → $", () => {
    expect(currencySymbol("EUR")).toBe("$");
  });
});
