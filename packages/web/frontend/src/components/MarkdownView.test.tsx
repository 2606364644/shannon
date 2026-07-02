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

  it("执行摘要 hero 置顶 + 含最高风险发现", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    const hero = container.querySelector('[data-testid="exec-summary-hero"]');
    expect(hero).not.toBeNull();
    expect(hero?.textContent).toContain("RCE");
    expect(hero?.textContent).toContain("INJ-01");
  });

  it("键值字段渲染成 key-value 行", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    expect(container.textContent).toContain("vulnerability_type");
    expect(container.textContent).toContain("CommandInjection");
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
