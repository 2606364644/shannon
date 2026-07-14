import { describe, it, expect } from "vitest";
import { computeStats, type ParsedTypeSummary } from "./report-stats";
import type { ParsedVulnBlock } from "../api/types";

function makeBlock(overrides: Partial<ParsedVulnBlock> = {}): ParsedVulnBlock {
  return {
    id: "TEST-VULN-01",
    prefix: "TEST",
    title: "test",
    starred: false,
    vulnType: "",
    fields: [],
    externallyExploitable: null,
    authRequired: null,
    confidence: null,
    verdict: null,
    raw: "",
    ...overrides,
  };
}

describe("computeStats", () => {
  it("total = 所有 vuln block 数", () => {
    const blocks = [
      makeBlock({ id: "INJ-VULN-01", prefix: "INJ" }),
      makeBlock({ id: "XSS-VULN-01", prefix: "XSS" }),
    ];
    expect(computeStats(blocks, new Set(), [], []).total).toBe(2);
  });

  it("typeAggs 按 prefix 分组，按 typeSummaries 顺序排序", () => {
    const blocks = [
      makeBlock({ id: "XSS-VULN-01", prefix: "XSS", vulnType: "Stored", authRequired: true }),
      makeBlock({ id: "INJ-VULN-01", prefix: "INJ", vulnType: "CommandInjection", authRequired: true }),
    ];
    const typeSummaries = [
      { prefix: "INJ", displayName: "Injection", count: 1, severityRangeRaw: "" },
      { prefix: "XSS", displayName: "XSS", count: 1, severityRangeRaw: "" },
    ];
    const stats = computeStats(blocks, new Set(), [], typeSummaries);
    expect(stats.typeAggs.map((t) => t.prefix)).toEqual(["INJ", "XSS"]);
  });

  it("severityDist 4 档总和 = total", () => {
    const blocks = [
      makeBlock({ id: "INJ-VULN-01", prefix: "INJ", vulnType: "CommandInjection", externallyExploitable: true, authRequired: false }),
      makeBlock({ id: "XSS-VULN-01", prefix: "XSS", vulnType: "Reflected", authRequired: true }),
    ];
    const stats = computeStats(blocks, new Set(), [], []);
    const sum = stats.severityDist.Critical + stats.severityDist.High + stats.severityDist.Medium + stats.severityDist.Low;
    expect(sum).toBe(blocks.length);
  });

  it("severityRange：组内 [Critical,Critical,High] → {max:Critical, min:High}", () => {
    const blocks = [
      makeBlock({ id: "INJ-VULN-01", prefix: "INJ", vulnType: "CommandInjection", authRequired: true }),
      makeBlock({ id: "INJ-VULN-02", prefix: "INJ", vulnType: "CommandInjection", authRequired: true }),
      makeBlock({ id: "INJ-VULN-03", prefix: "INJ", vulnType: "SQLi", authRequired: true }),
    ];
    // 01/02 base High + topRisk → Critical；03 base High 不在 topRisk
    const topRiskIds = new Set(["INJ-VULN-01", "INJ-VULN-02"]);
    const stats = computeStats(blocks, topRiskIds, [], []);
    const inj = stats.typeAggs.find((t) => t.prefix === "INJ")!;
    expect(inj.severityRange.max).toBe("Critical");
    expect(inj.severityRange.min).toBe("High");
    expect(inj.severityRangeLabel).toBe("Critical ~ High");
  });

  it("severityRangeLabel 单值时无 ~", () => {
    const blocks = [makeBlock({ id: "XSS-VULN-01", prefix: "XSS", vulnType: "Stored", authRequired: true })];
    const stats = computeStats(blocks, new Set(), [], []);
    expect(stats.typeAggs[0].severityRangeLabel).toBe("High");
  });

  it("publicCount = externallyExploitable===true 计数（false/null 不计）", () => {
    const blocks = [
      makeBlock({ id: "A-VULN-01", prefix: "A", externallyExploitable: true }),
      makeBlock({ id: "B-VULN-01", prefix: "B", externallyExploitable: false }),
      makeBlock({ id: "C-VULN-01", prefix: "C" }),
    ];
    expect(computeStats(blocks, new Set(), [], []).publicCount).toBe(1);
  });

  it("preAuthCount = authRequired===false 计数", () => {
    const blocks = [
      makeBlock({ id: "A-VULN-01", prefix: "A", authRequired: false }),
      makeBlock({ id: "B-VULN-01", prefix: "B", authRequired: true }),
    ];
    expect(computeStats(blocks, new Set(), [], []).preAuthCount).toBe(1);
  });

  it("topRisks 截取前 3 条", () => {
    const topRisks = [
      { text: "a", vulnIds: ["A-VULN-01"] },
      { text: "b", vulnIds: ["B-VULN-01"] },
      { text: "c", vulnIds: ["C-VULN-01"] },
      { text: "d", vulnIds: ["D-VULN-01"] },
    ];
    expect(computeStats([], new Set(), topRisks, []).topRisks.length).toBe(3);
  });

  it("displayName 走 TYPE_DISPLAY 规范（不取 typeSummaries 原文 Xss）", () => {
    const blocks = [makeBlock({ id: "XSS-VULN-01", prefix: "XSS", vulnType: "Stored", authRequired: true })];
    const typeSummaries = [{ prefix: "XSS", displayName: "Xss", count: 1, severityRangeRaw: "" }];
    expect(computeStats(blocks, new Set(), [], typeSummaries).typeAggs[0].displayName).toBe("XSS");
  });

  it("findingsText 从 typeSummaries 透传", () => {
    const blocks = [makeBlock({ id: "XSS-VULN-01", prefix: "XSS", vulnType: "Stored", authRequired: true })];
    const typeSummaries = [{ prefix: "XSS", displayName: "XSS", count: 1, severityRangeRaw: "", findingsText: "3 pre-auth 反射" }];
    expect(computeStats(blocks, new Set(), [], typeSummaries).typeAggs[0].findingsText).toBe("3 pre-auth 反射");
  });

  it("返回 attackChainCount 默认 0（computeStats 不算攻击链；实际值由 MarkdownView 覆盖）", () => {
    const blocks = [makeBlock({ id: "INJ-VULN-01", prefix: "INJ" })];
    expect(computeStats(blocks, new Set(), [], []).attackChainCount).toBe(0);
  });

  it("typeSummaries 空 prefix 时按 displayName 反查补全（中文报告场景）", () => {
    // 中文「按类型汇总」只给 displayName(Injection/XSS/...)，prefix 空 → 须反查 INJ/XSS
    // 5 个 AUTH 单点卡片驱动 AUTH count=5（对齐 hr_20260713-104726 现场场景：
    // 「单点漏洞数 5」全来自 AUTH 卡片，其它类型零召回）
    const blocks: ParsedVulnBlock[] = [
      makeBlock({ id: "AUTH-VULN-01", prefix: "AUTH" }),
      makeBlock({ id: "AUTH-VULN-02", prefix: "AUTH" }),
      makeBlock({ id: "AUTH-VULN-03", prefix: "AUTH" }),
      makeBlock({ id: "AUTH-VULN-04", prefix: "AUTH" }),
      makeBlock({ id: "AUTH-VULN-05", prefix: "AUTH" }),
    ];
    const typeSummaries: ParsedTypeSummary[] = [
      { prefix: "", displayName: "Injection", count: 0, severityRangeRaw: "" },
      { prefix: "", displayName: "XSS", count: 0, severityRangeRaw: "" },
      { prefix: "", displayName: "Auth", count: 5, severityRangeRaw: "" },
      { prefix: "", displayName: "Authz", count: 0, severityRangeRaw: "" },
      { prefix: "", displayName: "SSRF", count: 0, severityRangeRaw: "" },
    ];
    const aggs = computeStats(blocks, new Set(), [], typeSummaries).typeAggs;
    const byPrefix = Object.fromEntries(aggs.map((a) => [a.prefix, a.count]));
    expect(byPrefix).toEqual({ AUTH: 5, INJ: 0, XSS: 0, AUTHZ: 0, SSRF: 0 });
  });
});
