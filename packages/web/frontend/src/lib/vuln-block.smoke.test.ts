import { describe, it, expect } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { splitByVulnBlocks, inferSeverity } from "./vuln-block";
import type { Severity } from "../api/types";

// 真实 NodeGoat 报告（路径相对 repo 根）。CI 无此文件则整体 skip。
const REPORT = resolve(
  __dirname,
  "../../../../../workspaces/NodeGoat_20260630-002707/deliverables/comprehensive_security_assessment_report.md",
);
const skip = !existsSync(REPORT);

describe.skipIf(skip)("真实 NodeGoat 报告冒烟（解析 + severity 推断）", () => {
  const md = readFileSync(REPORT, "utf-8");
  const segs = splitByVulnBlocks(md);
  const vulns = segs.filter((s): s is Extract<typeof s, { type: "vuln" }> => s.type === "vuln");

  it("切出 25 个 ### 漏洞块（xss 13 + auth 10 + ssrf 2；inj 4 与 authz 7 是表格形式，无 ### 块）", () => {
    // 真实报告里 injection 走 ## Exploitation Queue 表格、authz 走 ## 裁决概览 表格，
    // 均无 ### VULN-NN 详情块 → 不进卡片，留在 prose 段渲染。
    expect(vulns.length).toBe(25);
  });

  it("无重复 id", () => {
    const ids = vulns.map((s) => s.block.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("每个块 id 形如 PREFIX-VULN-NN", () => {
    for (const s of vulns) {
      expect(s.block.id).toMatch(/^[A-Z]+-VULN-\d+$/);
    }
  });

  it("severity 分布合理（NodeGoat 高危居多：Critical+High > Medium+Low）", () => {
    const dist: Record<Severity, number> = { Critical: 0, High: 0, Medium: 0, Low: 0 };
    for (const s of vulns) dist[inferSeverity(s.block)]++;
    // eslint-disable-next-line no-console
    console.log("\n  severity 分布:", dist);
    expect(dist.Critical + dist.High + dist.Medium + dist.Low).toBe(vulns.length);
    expect(dist.Critical + dist.High).toBeGreaterThan(dist.Medium + dist.Low);
  });

  it("XSS-VULN-04（stored admin 投毒 ★ pre-auth）→ Critical", () => {
    const v = vulns.find((s) => s.block.id === "XSS-VULN-04");
    expect(v).toBeDefined();
    expect(inferSeverity(v!.block)).toBe("Critical");
  });

  it("AUTH-VULN-08（默认凭据 pre-auth）→ Critical", () => {
    const v = vulns.find((s) => s.block.id === "AUTH-VULN-08");
    expect(v).toBeDefined();
    expect(inferSeverity(v!.block)).toBe("Critical");
  });

  it("SSRF-VULN-01（完整响应 SSRF）→ High 或 Critical", () => {
    const v = vulns.find((s) => s.block.id === "SSRF-VULN-01");
    expect(v).toBeDefined();
    expect(["Critical", "High"]).toContain(inferSeverity(v!.block));
  });

  it("XSS-VULN-05（memo confidence low）→ 不超 Medium", () => {
    const v = vulns.find((s) => s.block.id === "XSS-VULN-05");
    expect(v).toBeDefined();
    expect(["Medium", "Low"]).toContain(inferSeverity(v!.block));
  });
});
