import { describe, it, expect } from "vitest";
import { splitAttackChainSection } from "./report-sections";

describe("splitAttackChainSection", () => {
  it("命中 ## 攻击链（多步利用路径）→ before/sectionMd/after + count（章节在文末）", () => {
    const md = [
      "# 安全评估报告",
      "",
      "## 执行摘要",
      "摘要内容",
      "",
      "## Authentication Vulnerabilities",
      "",
      "### AUTH-GN-EXPLORE-01: session fixation",
      "- **vulnerability_type:** Session",
      "",
      "## 攻击链（多步利用路径）",
      "",
      "### llm-chain-1: XSS -> 劫持",
      "步骤1",
      "",
      "### llm-chain-2: SSRF",
      "步骤2",
    ].join("\n");
    const r = splitAttackChainSection(md);
    expect(r).not.toBeNull();
    expect(r!.count).toBe(2);
    // before 含攻击链之前的全部（执行摘要 + auth 章节），不含攻击链标题行
    expect(r!.before).toContain("# 安全评估报告");
    expect(r!.before).toContain("AUTH-GN-EXPLORE-01");
    expect(r!.before).not.toMatch(/^## 攻击链/m);
    // sectionMd 不含 ## 标题行，含 llm-chain 条目
    expect(r!.sectionMd).not.toMatch(/^## /m);
    expect(r!.sectionMd).toContain("llm-chain-1");
    expect(r!.sectionMd).toContain("llm-chain-2");
    // 章节在文末 → after 为空
    expect(r!.after.trim()).toBe("");
  });

  it("标题措辞变体容错（英文 / 无括号）→ 仍命中", () => {
    const en = ["## Attack Chains", "", "### llm-chain-1: x", "步骤"].join("\n");
    expect(splitAttackChainSection(en)).not.toBeNull();
    const noParen = ["## 攻击链", "", "### llm-chain-1: x", "步骤"].join("\n");
    expect(splitAttackChainSection(noParen)).not.toBeNull();
  });

  it("攻击链章节在文中（after 非空）→ 三段正确分割", () => {
    const md = [
      "## 执行摘要",
      "摘要",
      "",
      "## 攻击链（多步利用路径）",
      "",
      "### llm-chain-1: x",
      "",
      "## 附录",
      "附录内容",
    ].join("\n");
    const r = splitAttackChainSection(md);
    expect(r).not.toBeNull();
    expect(r!.count).toBe(1);
    expect(r!.before).toContain("执行摘要");
    expect(r!.before).not.toContain("附录内容");
    expect(r!.sectionMd).toContain("llm-chain-1");
    expect(r!.after).toContain("## 附录");
    expect(r!.after).toContain("附录内容");
  });

  it("无攻击链章节 → 返回 null（老报告兼容）", () => {
    const md = [
      "# 报告",
      "",
      "## 执行摘要",
      "内容",
      "",
      "### AUTH-GN-EXPLORE-01: x",
      "- **vulnerability_type:** y",
    ].join("\n");
    expect(splitAttackChainSection(md)).toBeNull();
  });

  it("count 正确数 ### llm-chain-N（多条，含双位数）", () => {
    const md = [
      "## 攻击链",
      "### llm-chain-1: a",
      "### llm-chain-2: b",
      "### llm-chain-3: c",
      "### llm-chain-10: d",
    ].join("\n");
    expect(splitAttackChainSection(md)!.count).toBe(4);
  });

  it("攻击链章节内无 llm-chain 条目 → count=0（章节仍存在，返回非 null）", () => {
    const md = ["## 攻击链", "仅文字描述无条目"].join("\n");
    const r = splitAttackChainSection(md);
    expect(r).not.toBeNull();
    expect(r!.count).toBe(0);
  });

  it("职责边界：sectionMd 原样保留 llm-chain 内容，不解析为 vuln", () => {
    const md = [
      "## 攻击链",
      "",
      "### llm-chain-1: x",
      "- **类型:** xss",
      "- **严重程度:** critical",
    ].join("\n");
    const r = splitAttackChainSection(md);
    expect(r!.sectionMd).toContain("### llm-chain-1");
    expect(r!.sectionMd).toContain("严重程度");
  });

  it("before + sectionMd + after 拼接 ≈ 原文（无内容丢失）", () => {
    const md = [
      "## 执行摘要",
      "摘要",
      "",
      "## 攻击链",
      "",
      "### llm-chain-1: x",
      "",
      "## 附录",
      "附录",
    ].join("\n");
    const r = splitAttackChainSection(md)!;
    // 三段拼接（sectionMd 不含标题行，故补回 ## 攻击链）应覆盖原文所有非空内容
    const reassembled = r.before + "## 攻击链\n" + r.sectionMd + r.after;
    expect(reassembled).toContain("执行摘要");
    expect(reassembled).toContain("llm-chain-1");
    expect(reassembled).toContain("附录");
  });
});
