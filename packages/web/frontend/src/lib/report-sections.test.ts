import { describe, it, expect } from "vitest";
import { splitAttackChainSection, splitPocSection, parsePocEntries } from "./report-sections";

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

  it("回归：攻击链后跟 # 一级 evidence 区（无 ## 子标题）→ evidence 不被吞进 sectionMd", () => {
    // NodeGoat-20260811-165637~1 回归：renderer 去掉「## 已成功利用」section 标题后，
    // exploited 条目直接 `### VULN`，攻击链（## 二级）之后无任何 ## 二级标题。攻击链章节
    // 必须在遇到 `# 注入利用报告`（一级，新顶层报告区）时结束——否则 endIdx 滑到文档尾，
    // 整片 evidence 被吞进 sectionMd，singleVulnMd(=before+after) 零个 `### VULN` → 报告页
    // 漏洞计数归零。
    const md = [
      "# 安全评估报告",
      "",
      "## 执行摘要",
      "摘要",
      "",
      "## 攻击链（多步利用路径）",
      "未生成独立攻击链",
      "",
      "---",
      "",
      "# 注入利用报告",
      "",
      "### INJ-VULN-01: RCE via eval",
      "- **严重程度:** critical",
      "- **利用步骤:**",
      "  1. 注册账户",
      "  2. 发送 eval payload",
    ].join("\n");
    const r = splitAttackChainSection(md);
    expect(r).not.toBeNull();
    // evidence 没被吞进攻击链 section
    expect(r!.sectionMd).not.toContain("INJ-VULN-01");
    expect(r!.sectionMd).not.toContain("# 注入利用报告");
    // evidence 在 before+after（MarkdownView 的 singleVulnMd 拼接来源）里 → 计数非零
    const singleVulnMd = r!.before + r!.after;
    expect(singleVulnMd).toContain("### INJ-VULN-01");
    expect(singleVulnMd).toContain("注册账户");
  });
});

// —— PoC 章节切分 / 解析（spec 2026-07-24 §3.1）——
// 模拟后端 report endpoint 拼接：主报告 + \n\n---\n\n + PoC md（poc_generator.render_poc_md 产）
const POC_HEADING_LINE = "### ✓ INJ-VULN-01 · injection @ GET /login";
const POC_BODY = [
  "**置信度：已确认可复现** ｜ 认证：需登录 ｜ 来源：GitNexus",
  "",
  "**curl:**",
  "```bash",
  "curl -i -X GET 'https://t/login?u=%27'",
  "```",
  "",
  "**Burp Repeater (raw):**",
  "```http",
  "GET /login?u=%27 HTTP/1.1",
  "Host: t",
  "```",
].join("\n");

/** 后端拼接：{body}\n\n---\n\n{poc}（workspaces.py report endpoint） */
function buildReportWithPoc(pocMd: string, mainBody = "# 报告\n\n## 执行摘要\n摘要\n") {
  return `${mainBody}\n\n---\n\n${pocMd}`;
}

function buildPocMd(entries: string[], track = "白盒") {
  const lines = [
    `# 可利用漏洞 PoC 集合（${track}）`,
    "",
    "> 目标 host: https://t ｜ 共 N 条",
    "",
    "## 概览",
    "",
    "| ID | 类型 | 路径 | 认证 | 置信度 |",
    "|----|------|------|------|--------|",
    "",
    "## 详细 PoC",
    "",
  ];
  for (const e of entries) {
    lines.push(e);
    lines.push("");
    lines.push("---");
    lines.push("");
  }
  return lines.join("\n");
}

describe("splitPocSection", () => {
  it("命中 # 可利用漏洞 PoC 集合 → before / pocMd，before 剥离 --- 分隔线", () => {
    const cleanPoc = [
      "# 可利用漏洞 PoC 集合（白盒）",
      "",
      "## 详细 PoC",
      "",
      "### ✓ INJ-VULN-01 · injection @ GET /login",
      POC_BODY,
    ].join("\n");
    const md = buildReportWithPoc(cleanPoc);
    const r = splitPocSection(md);
    expect(r).not.toBeNull();
    expect(r!.before).toContain("# 报告");
    expect(r!.before).toContain("摘要");
    // 核心断言：后端拼接的 --- 分隔线被剥离，before 末尾不残留孤立 ---
    expect(r!.before.trim()).not.toMatch(/---$/);
    expect(r!.before).not.toContain("可利用漏洞 PoC 集合");
    expect(r!.pocMd.startsWith("# 可利用漏洞 PoC 集合")).toBe(true);
    expect(r!.pocMd).toContain("详细 PoC");
  });

  it("黑盒措辞 / 英文 Collection 变体容错 → 仍命中", () => {
    const zh = buildReportWithPoc("# 可利用漏洞 PoC 集合（黑盒）\n\n## 详细 PoC\n");
    expect(splitPocSection(zh)).not.toBeNull();
    const en = buildReportWithPoc("# Exploitable PoC Collection\n\n## detailed PoC\n");
    expect(splitPocSection(en)).not.toBeNull();
  });

  it("无 PoC 章节 → null（老报告 / 扫描未跑 PoC activity）", () => {
    expect(splitPocSection("# 报告\n\n## 执行摘要\n正文\n")).toBeNull();
  });
});

describe("parsePocEntries", () => {
  it("多条 ### → 每条提 ID，heading 剥离，body 含 curl/Burp", () => {
    const pocMd = buildPocMd([
      `${POC_HEADING_LINE}\n${POC_BODY}`,
      "### ● XSS-VULN-02 · xss @ GET /search\n**置信度：高置信**\n\n**curl:**\n```bash\nx\n```",
    ]);
    const entries = parsePocEntries(pocMd);
    expect(entries).toHaveLength(2);
    expect(entries[0].id).toBe("INJ-VULN-01");
    expect(entries[1].id).toBe("XSS-VULN-02");
    // heading 已剥离（条目体不以 ### 开头）
    expect(entries[0].md).not.toMatch(/^###\s/m);
    expect(entries[0].md).toContain("curl -i -X GET");
    expect(entries[0].md).toContain("Burp Repeater");
    // 概览表内容不进条目（只切 ## 详细 PoC 之后）
    expect(entries[0].md).not.toContain("概览");
  });

  it("GitNexus 轨 GN 前缀 ID 也能提（INJ-GN-08）", () => {
    const pocMd = buildPocMd([
      "### ✓ INJ-GN-08 · injection @ POST /api\n**置信度：已确认**\n\n**curl:**\n```bash\ny\n```",
    ]);
    const entries = parsePocEntries(pocMd);
    expect(entries[0].id).toBe("INJ-GN-08");
  });

  it("无 ## 详细 PoC 标题（仅概览 / 格式异常）→ []（不误并概览表）", () => {
    const pocMd = ["# 可利用漏洞 PoC 集合（白盒）", "", "## 概览", "", "| ID | 类型 |", "|--|--|"].join("\n");
    expect(parsePocEntries(pocMd)).toEqual([]);
  });

  it("无 ID 的 ### 条目跳过（不把描述段当 PoC 并入卡片）", () => {
    const pocMd = [
      "## 详细 PoC",
      "",
      "### 说明",
      "这是描述不是 PoC",
      "",
      "### ✓ INJ-VULN-01 · injection @ GET /x",
      "**curl:**",
      "```bash",
      "z",
      "```",
    ].join("\n");
    const entries = parsePocEntries(pocMd);
    expect(entries).toHaveLength(1);
    expect(entries[0].id).toBe("INJ-VULN-01");
    expect(entries[0].md).not.toContain("这是描述");
  });

  it("条目间 --- 分隔线剥离（条目体不含尾部孤立 ---）", () => {
    const pocMd = buildPocMd([`${POC_HEADING_LINE}\n${POC_BODY}`]);
    const entries = parsePocEntries(pocMd);
    expect(entries[0].md.trim()).not.toMatch(/---$/);
  });
});

// spec 2026-08-26 §8：PoC 单一呈现——web 报告页有并入详细 PoC 的卡，
// 卡内 PoC/最小见证行过滤（.md 原文不受影响，各文档自洽）。
import { stripCardPocLines } from "./report-sections";

describe("stripCardPocLines", () => {
  const body = [
    "**危害**",
    "x",
    "",
    "#### 漏洞细节",
    "- **PoC:** BODY-PAYLOAD",
    "- **最小见证:** BODY-WITNESS",
    "- **判定:** vulnerable",
  ].join("\n");

  it("过滤 PoC/最小见证行（zh/en 双标签），其余行原样", () => {
    const out = stripCardPocLines(body);
    expect(out).not.toContain("BODY-PAYLOAD");
    expect(out).not.toContain("BODY-WITNESS");
    expect(out).toContain("- **判定:** vulnerable");
    expect(out).toContain("**危害**");
  });

  it("英文标签 Minimal Witness 同滤；fence 内的同形文本不动（仅行首 kv 形态）", () => {
    const md = [
      "- **PoC:** p",
      "- **Minimal Witness:** w",
      "```bash",
      "- **PoC:** inside-fence-untouched",
      "```",
    ].join("\n");
    const out = stripCardPocLines(md);
    // fence 外两个 kv 行被滤、fence 内同形文本保留
    const outsideFence = out.split("```bash")[0];
    expect(outsideFence).not.toContain("**PoC:**");
    expect(outsideFence).not.toContain("**Minimal Witness:**");
    expect(out).toContain("inside-fence-untouched");
  });

  it("无匹配行 → 原样返回", () => {
    expect(stripCardPocLines("plain\nmd")).toBe("plain\nmd");
  });
});
