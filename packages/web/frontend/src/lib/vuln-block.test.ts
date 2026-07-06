import { describe, it, expect } from "vitest";
import {
  inferSeverity,
  SEVERITY_RANK,
  VULN_HEADING_RE,
  parseVulnBlock,
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

  it("authz 裁决概览（表格形式，无 ### VULN 块）→ 整段 prose", () => {
    const md = "## 裁决概览\n\n| ID | 端点 |\n|----|------|\n| AUTHZ-VULN-01 | /x |\n";
    const segs = splitByVulnBlocks(md);
    expect(segs.length).toBe(1);
    expect(segs[0].type).toBe("prose");
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
