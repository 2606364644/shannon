import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import i18n from "@/i18n";
import { ReportView } from "./ReportView";
import { ExecutiveSummary } from "./ExecutiveSummary";
import { StatsRow } from "./StatsRow";
import { VulnerabilityCard } from "./VulnerabilityCard";
import type { ReportData, ReportVulnerability } from "@/api/types";

// ── fixture：对齐 core pydantic schema（models/report_data.py）snake_case 直传 ──

const vuln: ReportVulnerability = {
  id: "XSS-VULN-01",
  type: "xss",
  vulnerability_type: "Stored",
  title: "备忘录存储型 XSS",
  severity: "high",
  confidence: "high",
  cwe_id: "CWE-79",
  externally_exploitable: true,
  merge_source: "both",
  merged_from: ["XSS-GN-13"],
  narrative: {
    cause: "渲染 memo 时未转义",
    impact: "会话窃取",
    remediation: "启用转义",
  },
  endpoints: [
    {
      method: "POST",
      path: "/memos",
      role: "write",
      auth: "isLoggedIn",
      params: ["memo"],
      route_registered_at: "app/routes/index.js:66",
      source_location: "app/routes/memos.js:13",
      sink_location: "app/views/memos.html:31",
    },
  ],
  affected_entries: [],
  dataflow_steps: [
    { label: "用户输入", file: "app/routes/memos.js", line: 13, protection: null },
    { label: "落库", file: "app/models/memo.js", line: 8, protection: "无" },
  ],
  poc: {
    witness_payload: '<img src=x onerror=alert(1)>',
    request: {
      method: "POST",
      url: "http://t/memos",
      headers: { Cookie: "connect.sid=abc" },
      body: "memo=<img src=x onerror=alert(1)>",
    },
    preconditions: "需登录（connect.sid）",
    expected_response: { indicator: "响应含未转义 payload", success_criteria: "onerror 触发" },
    curl: "curl -X POST 'http://t/memos' -d 'memo=<img src=x onerror=alert(1)>'",
    raw_http: null,
  },
  evidence: {
    verification: "static",
    dynamic_evidence: null,
    verdict: "vulnerable",
    code_snippet: "res.render('memos', { memo })",
    notes: null,
  },
  attack_chain_refs: [],
};

const dynamicVuln: ReportVulnerability = {
  ...vuln,
  id: "INJ-VULN-02",
  title: "NoSQL 注入（黑盒实测）",
  severity: "critical",
  merge_source: "llm-only",
  merged_from: [],
  evidence: {
    verification: "dynamic",
    dynamic_evidence: "HTTP/1.1 200 OK\n{\"uid\": 1000, \"role\": \"admin\"}",
    verdict: "exploited",
  },
};

const data: ReportData = {
  schema_version: 1,
  scan: { id: "scan-1", track: "whitebox", repo: "NodeGoat" },
  executive_summary: {
    narrative: "应用暴露面集中在备忘录模块",
    risk_level: "极高",
    top_risks: [
      { vuln_id: "XSS-VULN-01", reason: "公网可达且 pre-auth", priority: "P0" },
      { vuln_id: "INJ-VULN-02", reason: "实测已利用", priority: "P1" },
    ],
    remediation_order: "先修 XSS-VULN-01，再处理注入",
  },
  stats: {
    by_type: {
      xss: { count: 1, severity_range: "high", key_findings: "备忘录渲染缺转义" },
      injection: { count: 1, severity_range: "critical", key_findings: "NoSQL $where" },
      ssrf: { count: 0, severity_range: null, key_findings: null },
    },
    by_severity: { critical: 1, high: 1, medium: 0, low: 0 },
  },
  vulnerabilities: [vuln, dynamicVuln],
  attack_chains: [],
  qa: { passed: true, checks: [], reworked_ids: [] },
};

beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => i18n.changeLanguage("zh"));

describe("ReportView（JSON 纯渲染集成）", () => {
  it("渲染执行摘要叙事 + 统计行 + 全部漏洞卡", () => {
    render(<ReportView data={data} />);
    expect(screen.getByText(/应用暴露面集中在备忘录模块/)).toBeInTheDocument();
    expect(screen.getAllByTestId("report-vuln-card").length).toBe(2);
    // ID 同时出现在摘要锚点与卡片头（top-risk link → 卡片 id 锚点互通）
    expect(screen.getAllByText("XSS-VULN-01").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("INJ-VULN-02").length).toBeGreaterThanOrEqual(2);
  });

  it("qa.passed=false 时显式呈现 QA 未通过横幅", () => {
    render(<ReportView data={{ ...data, qa: { passed: false, checks: [{ check: "endpoints≥1", failed_ids: ["X-1"] }], reworked_ids: [] } }} />);
    expect(screen.getByTestId("report-qa-banner")).toBeInTheDocument();
    expect(screen.getByText(/QA 校验未通过/)).toBeInTheDocument();
  });
});

describe("ExecutiveSummary", () => {
  it("渲染叙事 + 风险等级 + 修复优先级", () => {
    render(<ExecutiveSummary summary={data.executive_summary!} />);
    expect(screen.getByText(/应用暴露面集中在备忘录模块/)).toBeInTheDocument();
    expect(screen.getByText(/风险等级: 极高/)).toBeInTheDocument();
    expect(screen.getByText(/先修 XSS-VULN-01/)).toBeInTheDocument();
  });

  it("top_risks 锚点链接到对应漏洞卡（href=#vuln_id）", () => {
    render(<ExecutiveSummary summary={data.executive_summary!} />);
    const a = screen.getByRole("link", { name: /XSS-VULN-01/ }) as HTMLAnchorElement;
    expect(a.getAttribute("href")).toBe("#XSS-VULN-01");
    expect(screen.getByRole("link", { name: /INJ-VULN-02/ })).toBeInTheDocument();
    // priority 徽章呈现
    expect(screen.getByText("P0")).toBeInTheDocument();
  });
});

describe("StatsRow", () => {
  it("按类型呈现 count（零计数类型也显示——数据自带，前端不推断）", () => {
    render(<StatsRow stats={data.stats!} />);
    const xss = screen.getByTestId("stat-type-xss");
    expect(within(xss).getByText("1")).toBeInTheDocument();
    const ssrf = screen.getByTestId("stat-type-ssrf");
    expect(within(ssrf).getByText("0")).toBeInTheDocument();
    expect(within(ssrf).getByText(/N\/A|—|—/)).toBeInTheDocument(); // 零计数 range 降级
  });

  it("呈现 key_findings 与 by_severity 分布", () => {
    render(<StatsRow stats={data.stats!} />);
    expect(screen.getByText(/备忘录渲染缺转义/)).toBeInTheDocument();
    // by_severity：critical 1 / high 1
    const sev = screen.getByTestId("stat-severity");
    expect(within(sev).getByText(/Critical/i)).toBeInTheDocument();
    expect(within(sev).getAllByText("1").length).toBeGreaterThanOrEqual(2);
  });
});

describe("VulnerabilityCard", () => {
  it("头部：ID + 标题 + 严重度徽章 + 双轨徽章 + merged_from 徽章", () => {
    render(<VulnerabilityCard v={vuln} />);
    expect(screen.getByText("XSS-VULN-01")).toBeInTheDocument();
    expect(screen.getByText(/备忘录存储型 XSS/)).toBeInTheDocument();
    const card = screen.getByTestId("report-vuln-card");
    expect(card.getAttribute("data-severity")).toBe("high");
    expect(screen.getByText(/双轨确认/)).toBeInTheDocument();
    expect(screen.getByText(/XSS-GN-13/)).toBeInTheDocument();
    expect(screen.getByText(/高危/)).toBeInTheDocument(); // vuln.severity.High zh
  });

  it("narrative 三段（成因/危害/修复建议）全部渲染", () => {
    render(<VulnerabilityCard v={vuln} />);
    expect(screen.getByText(/渲染 memo 时未转义/)).toBeInTheDocument();
    expect(screen.getByText(/会话窃取/)).toBeInTheDocument();
    expect(screen.getByText(/启用转义/)).toBeInTheDocument();
  });

  it("endpoints 一体表：Method/Path/参数/认证/路由注册/Source/Sink 七列全带行号", () => {
    render(<VulnerabilityCard v={vuln} />);
    const table = screen.getByTestId("vuln-endpoints");
    const head = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(head).toEqual(["Method", "Path", "参数", "认证", "路由注册", "Source", "Sink"]);
    const row = within(table).getAllByRole("row")[1];
    const cells = within(row).getAllByRole("cell").map((c) => c.textContent);
    expect(cells[0]).toContain("POST");
    expect(cells[1]).toContain("/memos");
    expect(cells[2]).toContain("memo");
    expect(cells[3]).toContain("isLoggedIn");
    expect(cells[4]).toContain("app/routes/index.js:66");
    expect(cells[5]).toContain("app/routes/memos.js:13");
    expect(cells[6]).toContain("app/views/memos.html:31");
  });

  it("POC 块：完整请求（方法/URL/头/体）+ 前置条件 + 预期响应 + witness + 复制 curl", () => {
    const writeText = vi.fn();
    Object.assign(navigator, { clipboard: { writeText } });
    render(<VulnerabilityCard v={vuln} />);
    const poc = screen.getByTestId("vuln-poc");
    expect(within(poc).getByText(/POST\s+http:\/\/t\/memos/)).toBeInTheDocument();
    expect(within(poc).getByText(/connect\.sid=abc/)).toBeInTheDocument();
    expect(within(poc).getByTestId("poc-body").textContent).toContain("memo=<img src=x onerror=alert(1)>");
    expect(within(poc).getByTestId("poc-witness").textContent).toContain("<img src=x onerror=alert(1)>");
    expect(within(poc).getByText(/需登录/)).toBeInTheDocument();
    expect(within(poc).getByText(/响应含未转义 payload/)).toBeInTheDocument();
    expect(within(poc).getByText(/onerror 触发/)).toBeInTheDocument();
    // curl 完整展示 + 复制按钮
    expect(within(poc).getByText(/curl -X POST 'http:\/\/t\/memos'/)).toBeInTheDocument();
    fireEvent.click(within(poc).getByTestId("copy-curl"));
    expect(writeText).toHaveBeenCalledWith("curl -X POST 'http://t/memos' -d 'memo=<img src=x onerror=alert(1)>'");
  });

  it("dataflow_steps 折叠区：默认收起，点击展开显示步 + file:line", () => {
    render(<VulnerabilityCard v={vuln} />);
    expect(screen.queryByTestId("dataflow-step")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("dataflow-toggle"));
    const steps = screen.getAllByTestId("dataflow-step");
    expect(steps.length).toBe(2);
    expect(steps[0].textContent).toContain("用户输入");
    expect(steps[0].textContent).toContain("app/routes/memos.js:13");
  });

  it("evidence：dynamic 实测输出突出显示（data-testid）", () => {
    render(<VulnerabilityCard v={dynamicVuln} />);
    const dyn = screen.getByTestId("dynamic-evidence");
    expect(dyn.textContent).toContain("uid");
    expect(screen.getByText(/动态实测|实测验证/)).toBeInTheDocument();
  });

  it("curl 缺失时由 request 确定性拼出复制内容（渲染层格式化，非推断）", () => {
    const writeText = vi.fn();
    Object.assign(navigator, { clipboard: { writeText } });
    render(
      <VulnerabilityCard
        v={{ ...vuln, poc: { ...vuln.poc!, curl: null } }}
      />,
    );
    fireEvent.click(screen.getByTestId("copy-curl"));
    const copied = writeText.mock.calls[0][0] as string;
    expect(copied).toContain("curl");
    expect(copied).toContain("http://t/memos");
    expect(copied).toContain("memo=<img src=x onerror=alert(1)>");
  });
});
