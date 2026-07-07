import { describe, it, expect } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { splitByVulnBlocks, inferSeverity } from "./vuln-block";

const REPORT = resolve(
  __dirname,
  "../../../../../workspaces/juice-shop_whitebox-1780587584138/deliverables/comprehensive_security_assessment_report.md",
);
const skip = !existsSync(REPORT);

describe.skipIf(skip)("juice-shop 报告切分诊断", () => {
  const md = readFileSync(REPORT, "utf-8");
  const segs = splitByVulnBlocks(md);
  const vulns = segs.filter((s): s is Extract<typeof s, { type: "vuln" }> => s.type === "vuln");

  it("切出的 vuln 块数量", () => {
    console.log("\n  总段数:", segs.length, "vuln 段:", vulns.length);
    expect(vulns.length).toBeGreaterThan(0);
  });

  it("前 5 个 vuln 块 id + severity", () => {
    const sample = vulns.slice(0, 5).map((s) => ({
      id: s.block.id,
      type: s.block.vulnType,
      ext: s.block.externallyExploitable,
      auth: s.block.authRequired,
      conf: s.block.confidence,
      star: s.block.starred,
      sev: inferSeverity(s.block),
    }));
    console.table(sample);
    expect(sample.length).toBeGreaterThan(0);
  });
});
