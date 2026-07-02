import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MarkdownView } from "./MarkdownView";

// 真实报告结构镜像（参考 NodeGoat comprehensive_security_assessment_report.md）：
// - H1 报告标题
// - H2 执行摘要（含「最高风险发现」编号列表，括号内 vuln ID）
// - H2 按漏洞类型汇总 / H3 类型小节
// - H2 <VulnType> / H3 <VULN-ID>: ... （键值列表 `- **key:** value`）
// - witness_payload 行内代码
const MD = `# 安全评估报告

## 执行摘要

- **目标：** NodeGoat
- **评估日期：** 2026-07-02

**总体结论：** 32 个漏洞

**最高风险发现（按业务影响排序）：**

1. **RCE**（INJ-01）：eval
2. **SSRF**（SSRF-01）：IMDSv1
3. **NoSQL $where 注入**（INJ-04）：threshold

## 按漏洞类型汇总

### Injection

Count: 4

## Injection

### INJ-VULN-01: eval RCE

- **vulnerability_type:** CommandInjection
- **verdict:** vulnerable
- **witness_payload:** \`preTax=res.send(...)\`
`;

describe("MarkdownView", () => {
  it("渲染 H1/H2/H3 标题", () => {
    render(<MarkdownView markdown={MD} />);
    // H1/H2/H3 都渲染；执行摘要出现于正文 H2 与 TOC，故用 heading role 精确取
    expect(screen.getByRole("heading", { level: 1, name: "安全评估报告" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "执行摘要" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: /INJ-VULN-01/ })).toBeInTheDocument();
  });

  it("TOC 含类型 + 执行摘要条目", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    const toc = container.querySelector('[data-testid="toc"]');
    expect(toc?.textContent).toContain("执行摘要");
    expect(toc?.textContent).toContain("Injection");
  });

  it("执行摘要 hero 置顶 + 含最高风险发现（vuln-ID 提取到专属元素）", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    const hero = container.querySelector('[data-testid="exec-summary-hero"]');
    expect(hero).not.toBeNull();
    // 整条文本仍可见（RCE / 描述）
    expect(hero?.textContent).toContain("RCE");
    // vuln-ID 被提取进专属 .kv-vuln-id 元素：回归若删掉提取（只保留整行文本）
    // 则 .kv-vuln-id 不存在，本断言失败。
    const vulnIdSpans = hero?.querySelectorAll(".kv-vuln-id");
    expect(vulnIdSpans?.length).toBeGreaterThan(0);
    expect(vulnIdSpans?.[0].textContent).toBe("INJ-01");
    // 多 vuln-ID 用 / 连接（INJ-04 等），守 join 行为
    const inj04 = Array.from(vulnIdSpans ?? []).find((s) =>
      s.textContent?.includes("INJ-04"),
    );
    expect(inj04).toBeDefined();
  });

  it("键值字段渲染成 key-value 行（结构化 .kv-row / .kv-key / .kv-val）", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    // 断言结构化处理存在：custom li 把 `- **key:** value` 拆成 .kv-row > (.kv-key + .kv-val)
    const kvRows = container.querySelectorAll("li.kv-row");
    expect(kvRows.length).toBeGreaterThan(0);
    // 找到 vulnerability_type 那一行
    const vtRow = Array.from(kvRows).find((li) =>
      li.querySelector(".kv-key")?.textContent?.includes("vulnerability_type"),
    );
    expect(vtRow).toBeDefined();
    expect(vtRow?.querySelector(".kv-key")?.textContent).toBe("vulnerability_type");
    expect(vtRow?.querySelector(".kv-val")?.textContent).toBe("CommandInjection");
    // 关键值不被重复渲染进同一个文本节点（key/val 分离的结构性证据）
    expect(vtRow?.querySelector(".kv-key")?.textContent).not.toContain("CommandInjection");
    expect(vtRow?.querySelector(".kv-val")?.textContent).not.toContain("vulnerability_type");
  });

  it("代码块带复制按钮（witness PoC 可复制）", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    const code = container.querySelector("code");
    expect(code).not.toBeNull();
    expect(code?.textContent).toContain("preTax=res.send(...)");
    expect(code?.querySelector(".copy-btn")).not.toBeNull();
  });

  it("无执行摘要时不渲染 hero（不写死结构）", () => {
    const { container } = render(<MarkdownView markdown={"# 报告\n\n正文"} />);
    expect(container.querySelector('[data-testid="exec-summary-hero"]')).toBeNull();
  });
});
