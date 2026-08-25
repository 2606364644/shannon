import { describe, it, expect } from "vitest";
import {
  inferSeverity,
  SEVERITY_RANK,
  VULN_HEADING_RE,
  VULN_ID_RE,
  parseVulnBlock,
  parseMetaSeverity,
  parseTableRowToBlock,
  isVulnTable,
  isSummaryTable,
  extractTableVulns,
  splitByVulnBlocks,
} from "./vuln-block";
import type { ParsedVulnBlock, Severity } from "../api/types";

/** 构造最小 ParsedVulnBlock，默认无信号（兜底 Medium）。 */
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

describe("SEVERITY_RANK", () => {
  it("档位数值递增", () => {
    expect(SEVERITY_RANK.Low).toBeLessThan(SEVERITY_RANK.Medium);
    expect(SEVERITY_RANK.Medium).toBeLessThan(SEVERITY_RANK.High);
    expect(SEVERITY_RANK.High).toBeLessThan(SEVERITY_RANK.Critical);
  });
});

describe("inferSeverity", () => {
  describe("base（按 vulnType + title 关键词）", () => {
    it("RCE / 命令注入 → High", () => {
      const s = inferSeverity(makeBlock({ vulnType: "CommandInjection (SSJS/RCE)", authRequired: true }));
      expect(s).toBe("High");
    });

    it("SSRF → High", () => {
      expect(inferSeverity(makeBlock({ vulnType: "Full-Response SSRF", authRequired: true }))).toBe("High");
    });

    it("SQLi / NoSQL $where → High", () => {
      expect(inferSeverity(makeBlock({ vulnType: "SQLi (NoSQL $where)", authRequired: true }))).toBe("High");
    });

    it("默认凭据 → High", () => {
      expect(inferSeverity(makeBlock({ vulnType: "Login_Flow_Logic", title: "硬编码默认凭据 admin/Admin_123", authRequired: true }))).toBe("High");
    });

    it("明文口令存储 → High", () => {
      expect(inferSeverity(makeBlock({ vulnType: "Token_Management_Issue", title: "口令明文存储与 === 比较", authRequired: true }))).toBe("High");
    });

    it("Session Fixation → High", () => {
      expect(inferSeverity(makeBlock({ vulnType: "Login_Flow_Logic", title: "登录后不轮换 session ID（Session Fixation）", authRequired: true }))).toBe("High");
    });

    it("明文 HTTP / 无 HSTS → High", () => {
      expect(inferSeverity(makeBlock({ vulnType: "Transport_Exposure", title: "明文 HTTP 无 HTTPS HSTS", authRequired: true }))).toBe("High");
    });

    it("Stored XSS → High", () => {
      expect(inferSeverity(makeBlock({ vulnType: "Stored", title: "经公开注册对 admin 投毒", authRequired: true }))).toBe("High");
    });

    it("IDOR / 垂直越权 → High", () => {
      expect(inferSeverity(makeBlock({ vulnType: "Horizontal", title: "IDOR — userId 取自 req.params", authRequired: true }))).toBe("High");
    });

    it("Reflected XSS（需认证，非 pre-auth）→ Medium", () => {
      expect(inferSeverity(makeBlock({ vulnType: "Reflected", title: "profile 字段属性回显", authRequired: true }))).toBe("Medium");
    });

    it("用户枚举 → Medium", () => {
      expect(inferSeverity(makeBlock({ vulnType: "Login_Flow_Logic", title: "差异化错误致用户名枚举", authRequired: true }))).toBe("Medium");
    });

    it("弱口令策略 → Medium", () => {
      expect(inferSeverity(makeBlock({ vulnType: "Login_Flow_Logic", title: "弱口令策略仅长度", authRequired: true }))).toBe("Medium");
    });

    it("无速率限制 / abuse → Medium", () => {
      expect(inferSeverity(makeBlock({ vulnType: "Abuse_Defenses_Missing", title: "无速率限制锁定监控", authRequired: true }))).toBe("Medium");
    });

    it("兜底类型 → Medium", () => {
      expect(inferSeverity(makeBlock({ vulnType: "Some Unknown Type", title: "怪东西", authRequired: true }))).toBe("Medium");
    });
  });

  describe("adjust · 公网 pre-auth +1", () => {
    it("RCE + 公网 + pre-auth → Critical", () => {
      expect(inferSeverity(makeBlock({ vulnType: "CommandInjection (RCE)", externallyExploitable: true, authRequired: false }))).toBe("Critical");
    });

    it("Reflected XSS + pre-auth → High（Medium +1）", () => {
      expect(inferSeverity(makeBlock({ vulnType: "Reflected", externallyExploitable: true, authRequired: false }))).toBe("High");
    });

    it("externallyExploitable=true 但需认证 → 不加档", () => {
      expect(inferSeverity(makeBlock({ vulnType: "Reflected", externallyExploitable: true, authRequired: true }))).toBe("Medium");
    });
  });

  describe("adjust · confidence low -1", () => {
    it("SQLi 需认证 + confidence low → Medium（High -1）", () => {
      expect(inferSeverity(makeBlock({ vulnType: "SQLi", externallyExploitable: true, authRequired: true, confidence: "low" }))).toBe("Medium");
    });

    it("confidence 大小写容错（LOW / Low 都降档）", () => {
      expect(inferSeverity(makeBlock({ vulnType: "SQLi", authRequired: true, confidence: "LOW" }))).toBe("Medium");
      expect(inferSeverity(makeBlock({ vulnType: "SQLi", authRequired: true, confidence: "Low" }))).toBe("Medium");
    });

    it("confidence high 不降档", () => {
      expect(inferSeverity(makeBlock({ vulnType: "SQLi", externallyExploitable: true, authRequired: false, confidence: "High" }))).toBe("Critical");
    });
  });

  describe("adjust · ★ / topRisk 至少 High、已 High 升 Critical", () => {
    it("★ 标题 + base Medium 需认证 → High（至少 High）", () => {
      expect(inferSeverity(makeBlock({ vulnType: "Reflected", title: "profile 命名陷阱", starred: true, authRequired: true }))).toBe("High");
    });

    it("★ 标题 + base High + pre-auth → Critical", () => {
      expect(inferSeverity(makeBlock({ vulnType: "Stored", title: "admin 投毒", starred: true, externallyExploitable: true, authRequired: false }))).toBe("Critical");
    });

    it("topRiskIds 含本 id（base Medium 不 pre-auth）→ High", () => {
      expect(inferSeverity(
        makeBlock({ id: "XSS-VULN-09", vulnType: "Reflected", externallyExploitable: true, authRequired: true }),
        new Set(["XSS-VULN-09"]),
      )).toBe("High");
    });

    it("topRiskIds 不含本 id → 不加档", () => {
      expect(inferSeverity(
        makeBlock({ id: "XSS-VULN-09", vulnType: "Reflected", externallyExploitable: true, authRequired: true }),
        new Set(["OTHER-VULN-01"]),
      )).toBe("Medium");
    });
  });

  describe("clamp", () => {
    it("下界：兜底 Medium + confidence low → Low", () => {
      expect(inferSeverity(makeBlock({ vulnType: "Unknown", authRequired: true, confidence: "low" }))).toBe("Low");
    });

    it("上界：High + pre-auth + starred 仍为 Critical（不超）", () => {
      expect(inferSeverity(makeBlock({ vulnType: "Stored", starred: true, externallyExploitable: true, authRequired: false }))).toBe("Critical");
    });
  });

  describe("返回值类型守卫", () => {
    it("始终返回合法 Severity", () => {
      const valid: Severity[] = ["Critical", "High", "Medium", "Low"];
      const s = inferSeverity(makeBlock({ vulnType: "Anything" }));
      expect(valid).toContain(s);
    });
  });
});

describe("VULN_HEADING_RE", () => {
  it("匹配 ### PREFIX-VULN-NUM", () => {
    expect(VULN_HEADING_RE.test("### XSS-VULN-04 — title")).toBe(true);
    expect(VULN_HEADING_RE.test("### INJ-VULN-01: title")).toBe(true);
    expect(VULN_HEADING_RE.test("### AUTHZ-VULN-10 — x")).toBe(true);
  });
  it("不匹配普通 ### 标题", () => {
    expect(VULN_HEADING_RE.test("### Injection")).toBe(false);
    expect(VULN_HEADING_RE.test("### 普通标题")).toBe(false);
    expect(VULN_HEADING_RE.test("## XSS-VULN-01")).toBe(false);
  });
});

describe("parseVulnBlock", () => {
  const RAW = [
    "### XSS-VULN-04 — 经公开注册对 admin 投毒 ★ 首要目标",
    "- **vulnerability_type:** Stored",
    "- **externally_exploitable:** true（注入公开）| **authentication_required:** false（注入路由公开）",
    "- **source:** `POST /signup` body firstName",
    "- **verdict:** vulnerable | **confidence:** high",
    "- **witness_payload:** `<img src=x onerror=alert(1)>`（作为 firstName 注册）",
  ].join("\n");

  it("解析 id / prefix", () => {
    const b = parseVulnBlock(RAW);
    expect(b.id).toBe("XSS-VULN-04");
    expect(b.prefix).toBe("XSS");
  });

  it("title 去 ★ 后缀 + starred=true", () => {
    const b = parseVulnBlock(RAW);
    expect(b.title).toBe("经公开注册对 admin 投毒");
    expect(b.starred).toBe(true);
  });

  it("无 ★ 时 starred=false（冒号分隔符兼容）", () => {
    const b = parseVulnBlock("### XSS-VULN-01: login 回显\n- **vulnerability_type:** Reflected");
    expect(b.starred).toBe(false);
    expect(b.title).toBe("login 回显");
  });

  it("提取关键字段（含 | 同行多字段）", () => {
    const b = parseVulnBlock(RAW);
    expect(b.vulnType).toBe("Stored");
    expect(b.externallyExploitable).toBe(true);
    expect(b.authRequired).toBe(false);
    expect(b.confidence).toBe("high");
    expect(b.verdict).toBe("vulnerable");
  });

  it("confidence 大小写容错（Medium → med）", () => {
    const b = parseVulnBlock("### AUTH-VULN-09 — secret\n- **confidence:** Medium");
    expect(b.confidence).toBe("med");
  });

  it("缺失字段为 null（authz 表格形式无这些字段）", () => {
    const b = parseVulnBlock("### AUTHZ-VULN-01 — IDOR\n- **vulnerability_type:** Horizontal");
    expect(b.externallyExploitable).toBeNull();
    expect(b.authRequired).toBeNull();
    expect(b.confidence).toBeNull();
    expect(b.verdict).toBeNull();
  });

  it("提取 witness_payload 反引号内代码", () => {
    const b = parseVulnBlock(RAW);
    expect(b.witnessPayload).toBe("<img src=x onerror=alert(1)>");
  });

  it("witness_payload 后跟独立 fenced code → 提取 code 内容", () => {
    const raw = [
      "### INJ-VULN-01: eval RCE",
      "- **vulnerability_type:** CommandInjection",
      "- **witness_payload:**",
      "  ```bash",
      "  preTax=res.send(...)",
      "  ```",
    ].join("\n");
    expect(parseVulnBlock(raw).witnessPayload).toBe("preTax=res.send(...)");
  });

  it("kv-list fields 提取（冒号守卫，多字段行只取首字段）", () => {
    const b = parseVulnBlock(RAW);
    const keys = b.fields.map((f) => f.key);
    expect(keys).toContain("vulnerability_type");
    expect(keys).toContain("source");
    expect(keys).toContain("verdict");
    expect(keys).toContain("witness_payload");
  });
});

describe("splitByVulnBlocks", () => {
  it("无 vuln 块 → 单个 prose 段", () => {
    const md = "# 标题\n\n一些 prose。\n\n## 章节\n\n更多 prose。";
    const segs = splitByVulnBlocks(md);
    expect(segs.length).toBe(1);
    expect(segs[0].type).toBe("prose");
  });

  it("单 vuln 块前后有 prose → 3 段", () => {
    const md = [
      "# 报告",
      "",
      "## XSS",
      "",
      "### XSS-VULN-01 — reflected",
      "- **vulnerability_type:** Reflected",
      "",
      "## 认证",
      "",
    ].join("\n");
    const segs = splitByVulnBlocks(md);
    expect(segs.map((s) => s.type)).toEqual(["prose", "vuln", "prose"]);
    if (segs[1].type === "vuln") {
      expect(segs[1].block.id).toBe("XSS-VULN-01");
    }
  });

  it("多个连续 vuln 块 → 各自段", () => {
    const md = [
      "### XSS-VULN-01 — a",
      "- **vulnerability_type:** Reflected",
      "### XSS-VULN-02 — b",
      "- **vulnerability_type:** Stored",
    ].join("\n");
    const segs = splitByVulnBlocks(md);
    expect(segs.length).toBe(2);
    expect(segs.every((s) => s.type === "vuln")).toBe(true);
  });

  it("authz 裁决概览（表格形式，首列=ID）→ 解析成 vuln 段", () => {
    const md = "## 裁决概览\n\n| ID | 端点 |\n|----|------|\n| AUTHZ-VULN-01 | /x |\n";
    const segs = splitByVulnBlocks(md);
    const vulns = segs.filter((s) => s.type === "vuln");
    expect(vulns.length).toBe(1);
    if (vulns[0].type === "vuln") {
      expect(vulns[0].block.id).toBe("AUTHZ-VULN-01");
      expect(vulns[0].block.prefix).toBe("AUTHZ");
    }
  });

  it("普通表（首列非 ID，如 | 类型 | 数量 |）→ 不拆，留 prose", () => {
    const md = "| 类型 | 数量 |\n|------|------|\n| INJ | 4 |\n";
    const segs = splitByVulnBlocks(md);
    expect(segs.filter((s) => s.type === "vuln").length).toBe(0);
    expect(segs.some((s) => s.type === "prose")).toBe(true);
  });

  it("vuln 块遇到 ## 更高级标题结束", () => {
    const md = [
      "### INJ-VULN-01 — rce",
      "- **vulnerability_type:** CommandInjection",
      "",
      "## 认证报告",
      "",
      "正文",
    ].join("\n");
    const segs = splitByVulnBlocks(md);
    expect(segs.map((s) => s.type)).toEqual(["vuln", "prose"]);
  });

  it("prose 段保留原始文本（含空行）", () => {
    const md = "前 prose\n\n### INJ-VULN-01 — x\n- **vulnerability_type:** SQLi";
    const segs = splitByVulnBlocks(md);
    expect(segs[0].type).toBe("prose");
    if (segs[0].type === "prose") {
      expect(segs[0].md).toContain("前 prose");
    }
  });
});

describe("VULN_ID_RE", () => {
  it("匹配 PREFIX-VULN-NN", () => {
    expect(VULN_ID_RE.test("INJ-VULN-01")).toBe(true);
    expect(VULN_ID_RE.test("AUTHZ-VULN-10")).toBe(true);
  });
  it("不匹配短形式或非漏洞 id", () => {
    expect(VULN_ID_RE.test("INJ-01")).toBe(false);
    expect(VULN_ID_RE.test("ID")).toBe(false);
    expect(VULN_ID_RE.test("inj-vuln-01")).toBe(false);
  });
});

describe("isVulnTable", () => {
  it("首列头=ID 且首列数据=VULN id → true", () => {
    expect(isVulnTable("ID", "INJ-VULN-01")).toBe(true);
    expect(isVulnTable("id", "AUTHZ-VULN-07")).toBe(true);
  });
  it("首列非 ID 或数据非 vuln id → false", () => {
    expect(isVulnTable("类型", "INJ")).toBe(false);
    expect(isVulnTable("ID", "INJ-01")).toBe(false);
    expect(isVulnTable("ID", "")).toBe(false);
  });
});

describe("parseTableRowToBlock", () => {
  it("Injection 表行（6 列）→ vulnType 来自「类型」列，auth 来自「认证」列", () => {
    const headers = ["ID", "类型", "源", "Sink", "认证", "置信度"];
    const row = ["INJ-VULN-01", "CommandInjection (SSJS/RCE)", "`preTax`", "`eval()`", "isLoggedIn", "high"];
    const b = parseTableRowToBlock(headers, row);
    expect(b.id).toBe("INJ-VULN-01");
    expect(b.prefix).toBe("INJ");
    expect(b.vulnType).toBe("CommandInjection (SSJS/RCE)");
    expect(b.authRequired).toBe(true);
    expect(b.confidence).toBe("high");
    expect(b.externallyExploitable).toBeNull();
    expect(b.witnessPayload).toBeUndefined();
  });

  it("Authz 表行（5 列，不同表头）→ title 来自「核心缺陷」列", () => {
    const headers = ["ID", "端点", "类型", "置信度", "核心缺陷"];
    const row = ["AUTHZ-VULN-01", "GET /allocations/:userId", "Horizontal", "high", "userId 取自 req.params"];
    const b = parseTableRowToBlock(headers, row);
    expect(b.id).toBe("AUTHZ-VULN-01");
    expect(b.vulnType).toBe("Horizontal");
    expect(b.title).toBe("userId 取自 req.params");
    expect(b.authRequired).toBeNull();
    // 非 ID 列都进 fields
    const keys = b.fields.map((f) => f.key);
    expect(keys).toContain("类型");
    expect(keys).toContain("核心缺陷");
    expect(keys).not.toContain("ID");
  });

  it("inferSeverity 对表格 block 工作：CommandInjection + isLoggedIn → High，带 topRiskIds → Critical", () => {
    const headers = ["ID", "类型", "源", "Sink", "认证", "置信度"];
    const row = ["INJ-VULN-01", "CommandInjection (SSJS/RCE)", "`preTax`", "`eval()`", "isLoggedIn", "high"];
    const b = parseTableRowToBlock(headers, row);
    expect(inferSeverity(b)).toBe("High");
    expect(inferSeverity(b, new Set(["INJ-VULN-01"]))).toBe("Critical");
  });
});

describe("extractTableVulns", () => {
  it("漏洞表 → vuln 段；表前后的 prose 保留", () => {
    const md = "## Exploitation Queue\n\n| ID | 类型 |\n|----|------|\n| INJ-VULN-01 | RCE |\n| INJ-VULN-02 | RCE |\n\n后续 prose。";
    const segs = extractTableVulns(md);
    const vulns = segs.filter((s) => s.type === "vuln");
    expect(vulns.length).toBe(2);
    expect(segs[0].type).toBe("prose");
    expect(segs[segs.length - 1].type).toBe("prose");
  });

  it("普通表（首列非 ID）→ 留 prose，不产 vuln 段", () => {
    const md = "| 类型 | 数量 |\n|------|------|\n| INJ | 4 |\n";
    const segs = extractTableVulns(md);
    expect(segs.filter((s) => s.type === "vuln").length).toBe(0);
    expect(segs[0].type).toBe("prose");
  });
});

describe("速查表跳过提取（终审 F1：防双计/双卡/DOM id 重复）", () => {
  // 渲染层确定性注入的「漏洞速查表」（report_assembler.render_summary_table）：
  // 首列=ID + 接口/严重度列，每行与同 ID 的 `### ` 完整卡并存。旧逻辑按漏洞表
  // 提取每行成迷你块 → 报告页统计翻倍、每洞双卡、DOM id 重复。修法：识别签名
  // （含接口/Endpoint 列且含严重度/Severity 列）整表跳过，普通漏洞表格零变化。

  const SUMMARY_MD = [
    "## 漏洞速查表",
    "",
    "### 注入漏洞",
    "",
    "| ID | 漏洞 | 接口 | 参数 | 严重度 | 验证 | 置信度 |",
    "|---|---|---|---|---|---|---|",
    "| INJ-VULN-01 | 命令注入 | POST /contributions | preTax | 严重 | 静态分析 | 高 |",
    "",
    "---",
    "",
    "## 注入漏洞",
    "",
    "### INJ-VULN-01 注入漏洞：命令注入",
    "严重程度：严重 ｜ CWE-95 ｜ 验证：静态分析 ｜ 置信度：高",
    "",
    "**漏洞说明**",
    "preTax 直接传入 eval()。",
  ].join("\n");

  it("速查表 + ### 卡同 ID → 解析块数 = ### 卡数（无迷你块、无重复 id），速查表留 prose", () => {
    const segs = splitByVulnBlocks(SUMMARY_MD);
    const vulnSegs = segs.filter((s): s is Extract<typeof s, { type: "vuln" }> => s.type === "vuln");
    expect(vulnSegs.length).toBe(1); // 只有 ### 完整卡，无表格迷你块
    expect(vulnSegs[0].block.id).toBe("INJ-VULN-01");
    const ids = vulnSegs.map((s) => s.block.id);
    expect(new Set(ids).size).toBe(ids.length); // 无重复 DOM id
    // 速查表整表留在 prose（react-markdown 原样渲染）
    const prose = segs
      .filter((s): s is Extract<typeof s, { type: "prose" }> => s.type === "prose")
      .map((s) => s.md)
      .join("\n");
    expect(prose).toContain("| INJ-VULN-01 | 命令注入 |");
  });

  it("en 速查表（Endpoint/Severity 列）同样跳过", () => {
    const md = [
      "## Vulnerability Summary Table",
      "",
      "| ID | Vulnerability | Endpoint | Parameters | Severity | Verification | Confidence |",
      "|---|---|---|---|---|---|---|",
      "| INJ-VULN-01 | Command Injection | POST /contributions | preTax | Critical | Static Analysis | High |",
      "",
      "### INJ-VULN-01 注入漏洞：命令注入",
      "- **vulnerability_type:** CommandInjection",
    ].join("\n");
    const vulnSegs = splitByVulnBlocks(md).filter(
      (s): s is Extract<typeof s, { type: "vuln" }> => s.type === "vuln",
    );
    expect(vulnSegs.length).toBe(1);
    expect(vulnSegs[0].block.id).toBe("INJ-VULN-01");
  });

  it("isSummaryTable：接口+严重度签名命中 zh/en；普通漏洞表/汇总表不命中", () => {
    expect(isSummaryTable(["ID", "漏洞", "接口", "参数", "严重度", "验证", "置信度"])).toBe(true);
    expect(
      isSummaryTable(["ID", "Vulnerability", "Endpoint", "Parameters", "Severity", "Verification", "Confidence"]),
    ).toBe(true);
    // Injection Queue / Authz 裁决概览（无接口+严重度列组合）→ 不是速查表
    expect(isSummaryTable(["ID", "类型", "源", "Sink", "认证", "置信度"])).toBe(false);
    expect(isSummaryTable(["ID", "端点", "类型", "置信度", "核心缺陷"])).toBe(false);
    expect(isSummaryTable(["类型", "数量"])).toBe(false);
  });

  it("普通漏洞表行为零变化：仍逐行提取成 vuln 段", () => {
    const md = "| ID | 类型 |\n|----|------|\n| INJ-VULN-01 | RCE |\n";
    const segs = extractTableVulns(md);
    expect(segs.filter((s) => s.type === "vuln").length).toBe(1);
  });
});

describe("GitNexus 轨 ID 兼容（双轨隔离设计，-GN- 须保留，不统一为 -VULN-）", () => {
  // 背景：GitNexus 轨产 PREFIX-GN-N / PREFIX-GN-EXPLORE-N / PREFIX-GN-LOGIC-N（PREFIX=类前缀，
  // GN=GitNexus 缩写，EXPLORE/LOGIC=深度 agent 子类型）；LLM 轨产 PREFIX-VULN-N。两套 ID 是有意的
  // 双轨隔离（防并集 ID 碰撞，来源另有 source_track/merge_source 字段承载）。原 VULN_HEADING_RE /
  // VULN_ID_RE 只认 -VULN-，是 2026-07-06 为 LLM 单轨报告写的、没跟上双轨 → GitNexus 漏洞进报告后
  // 前端数 0（回归 hr_20260713-104726）。放宽正则兼容 -GN- 系列，且保留原始 id 不变形。

  it("VULN_HEADING_RE 匹配 ### PREFIX-GN-... 标题", () => {
    expect(VULN_HEADING_RE.test("### AUTH-GN-EXPLORE-01 — appSecret 签名绕过")).toBe(true);
    expect(VULN_HEADING_RE.test("### AUTH-GN-LOGIC-01 — session 固定")).toBe(true);
    expect(VULN_HEADING_RE.test("### INJ-GN-02 — eval RCE")).toBe(true);
    expect(VULN_HEADING_RE.test("### AUTHZ-GN-01 — IDOR")).toBe(true);
    expect(VULN_HEADING_RE.test("### SSRF-GN-03 — url fetch")).toBe(true);
  });

  it("VULN_ID_RE 匹配 PREFIX-GN-... id；不匹配 attack chain（llm-chain-N 小写，非漏洞）", () => {
    expect(VULN_ID_RE.test("AUTH-GN-EXPLORE-01")).toBe(true);
    expect(VULN_ID_RE.test("AUTH-GN-LOGIC-01")).toBe(true);
    expect(VULN_ID_RE.test("INJ-GN-02")).toBe(true);
    expect(VULN_ID_RE.test("AUTHZ-GN-01")).toBe(true);
    expect(VULN_ID_RE.test("llm-chain-1")).toBe(false);
  });

  it("parseVulnBlock 保留原始 GN id（不变形为 -VULN-）+ prefix 取类前缀", () => {
    const b = parseVulnBlock(
      "### AUTH-GN-EXPLORE-01: appSecret 签名校验绕过\n- **vulnerability_type:** Token_Management_Issue",
    );
    expect(b.id).toBe("AUTH-GN-EXPLORE-01"); // 原样：inferSeverity 用 topRiskIds.has(id) 联动，变形会失效
    expect(b.prefix).toBe("AUTH");
    expect(b.title).toBe("appSecret 签名校验绕过");
  });

  it("splitByVulnBlocks 切出 GN 漏洞段（回归 hr_20260713-104726 报告页 0 漏洞）", () => {
    const md = [
      "# 安全评估报告",
      "",
      "## Authentication Vulnerabilities",
      "",
      "### AUTH-GN-EXPLORE-01: appSecret 签名校验绕过",
      "- **vulnerability_type:** Token_Management_Issue",
      "",
      "### AUTH-GN-EXPLORE-02: CSRF 全局关闭",
      "- **vulnerability_type:** CSRF",
    ].join("\n");
    const segs = splitByVulnBlocks(md);
    const vulns = segs.filter((s) => s.type === "vuln");
    expect(vulns.length).toBe(2);
    if (vulns[0].type === "vuln") {
      expect(vulns[0].block.id).toBe("AUTH-GN-EXPLORE-01");
      expect(vulns[0].block.prefix).toBe("AUTH");
    }
  });

  it("isVulnTable 识别 GN id 首列", () => {
    expect(isVulnTable("ID", "AUTH-GN-EXPLORE-01")).toBe(true);
    expect(isVulnTable("ID", "INJ-GN-02")).toBe(true);
  });

  it("parseTableRowToBlock: GN id → prefix 取类前缀、id 保留", () => {
    const b = parseTableRowToBlock(["ID", "类型"], ["AUTH-GN-EXPLORE-01", "Token_Management_Issue"]);
    expect(b.id).toBe("AUTH-GN-EXPLORE-01");
    expect(b.prefix).toBe("AUTH");
  });
});

describe("parseVulnBlock · 纯 ID 标题 prefix 提取（回归 NodeGoat 报告页「34 卡片 + 类型卡 0」）", () => {
  // 现场 NodeGoat-20260729-194022：报告生成层格式不一致——INJ 标题带 ` — 描述`，
  // XSS/AUTH/AUTHZ/SSRF 标题是纯 ID（### XSS-VULN-01 行尾即换行、无描述）。
  // 旧 parseVulnBlock 用 `id === fallbackTitle ? ""` 判 prefix：纯 ID 标题下 id 与
  // fallbackTitle 都等于纯 ID → 判等 → prefix="" → 34 个漏洞全归空 prefix 组，
  // TypeSummaryCards 把空 prefix 组渲染成无标识「34 卡片」，类型卡只剩 INJ=6、其余 0。

  it("纯 ID 标题（无描述）→ prefix 正确取类前缀，非空串", () => {
    const b = parseVulnBlock("### XSS-VULN-01\n- **Verdict:** vulnerable");
    expect(b.id).toBe("XSS-VULN-01");
    expect(b.prefix).toBe("XSS");
  });

  it("带描述标题 → prefix 仍正确（不回归）", () => {
    const b = parseVulnBlock("### INJ-VULN-01 — eval RCE\n- **Verdict:** vulnerable");
    expect(b.id).toBe("INJ-VULN-01");
    expect(b.prefix).toBe("INJ");
  });

  it("splitByVulnBlocks 纯 ID + 带描述混合 → 各类 prefix 正确、无空串组", () => {
    const md = [
      "## Injection Vulnerabilities",
      "### INJ-VULN-01 — eval RCE",
      "- **Verdict:** vulnerable",
      "",
      "## Cross-Site Scripting (XSS)",
      "### XSS-VULN-01",
      "- **Verdict:** vulnerable",
      "### XSS-VULN-02",
      "- **Verdict:** vulnerable",
    ].join("\n");
    const segs = splitByVulnBlocks(md);
    const byPrefix: Record<string, number> = {};
    for (const s of segs)
      if (s.type === "vuln") byPrefix[s.block.prefix] = (byPrefix[s.block.prefix] || 0) + 1;
    expect(byPrefix).toEqual({ INJ: 1, XSS: 2 });
  });
});

describe("parseMetaSeverity · 新版卡片元信息行真数据优先（spec 2026-08-25 §5）", () => {
  // 新版四要素卡首行元信息：`严重程度：X ｜ CWE-xx ｜ 验证：… ｜ 置信度：…`（X ∈ 严重/高危/中危/低危）。
  // severity 从"关键词启发式猜测"升级为"读渲染层写入的真数据"；旧报告无该行 → null → 启发式兜底。
  const CARD_MD = [
    "### INJ-VULN-01 注入漏洞：命令注入",
    "严重程度：严重 ｜ CWE-95 ｜ 验证：静态分析 ｜ 置信度：高（双轨确认）",
    "",
    "**漏洞说明**",
    "preTax 直接传入 eval()。",
  ].join("\n");

  const vulnSegs = (md: string) =>
    splitByVulnBlocks(md).filter(
      (s): s is Extract<typeof s, { type: "vuln" }> => s.type === "vuln",
    );

  it("读元信息行真数据：严重 → Critical，inferSeverity 同步优先", () => {
    const seg = vulnSegs(CARD_MD).find((s) => s.block.id === "INJ-VULN-01");
    expect(seg).toBeDefined();
    // 启发式本身只会给 Medium（中文标题无英文关键词命中）→ Critical 只能来自元信息行
    expect(parseMetaSeverity(seg!.block)).toBe("Critical");
    expect(inferSeverity(seg!.block)).toBe("Critical");
  });

  it("四档中文词全映射（严重/高危/中危/低危 → Critical/High/Medium/Low）", () => {
    const cases = [
      ["严重", "Critical"],
      ["高危", "High"],
      ["中危", "Medium"],
      ["低危", "Low"],
    ] as const;
    for (const [zh, sev] of cases) {
      const b = parseVulnBlock(`### INJ-VULN-01 注入漏洞：命令注入\n严重程度：${zh} ｜ CWE-95`);
      expect(parseMetaSeverity(b)).toBe(sev);
      expect(inferSeverity(b)).toBe(sev);
    }
  });

  it("元信息行是权威：优先于启发式的 ★/topRisk 升档", () => {
    const b = parseVulnBlock("### XSS-VULN-02 反射 ★\n严重程度：低危 ｜ 验证：静态分析");
    expect(b.starred).toBe(true);
    // 无元信息时启发式会给 High（Medium + ★ 至少 High）；有元信息 → 低危即 Low
    expect(parseMetaSeverity(b)).toBe("Low");
    expect(inferSeverity(b)).toBe("Low");
  });

  it("旧报告无元信息行 → null，inferSeverity 落回启发式（不抛错）", () => {
    const seg = vulnSegs("### INJ-VULN-02 Old style\n\nsome body").find(
      (s) => s.block.id === "INJ-VULN-02",
    );
    expect(seg).toBeDefined();
    expect(parseMetaSeverity(seg!.block)).toBeNull();
    expect(["Critical", "High", "Medium", "Low"]).toContain(inferSeverity(seg!.block));
  });

  it("老格式 severity 行（- **严重程度:** high，冒号后有 **）不误读 → 落回启发式", () => {
    const b = parseVulnBlock("### INJ-VULN-03 eval RCE\n- **严重程度:** high");
    expect(parseMetaSeverity(b)).toBeNull();
    expect(inferSeverity(b)).toBe("High"); // 启发式：eval 关键词 → High
  });
});
