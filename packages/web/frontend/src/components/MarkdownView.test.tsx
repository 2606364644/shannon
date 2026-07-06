import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
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
- **witness_payload:**
  \`\`\`bash
  preTax=res.send(...)
  \`\`\`
`;

describe("MarkdownView", () => {
  it("渲染 H1/H2 标题；vuln 块进卡片（INJ-VULN-01 不再是 heading）", () => {
    render(<MarkdownView markdown={MD} />);
    expect(screen.getByRole("heading", { level: 1, name: "安全评估报告" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "执行摘要" })).toBeInTheDocument();
    // prose 段的 ### 类型小节仍是 heading
    expect(screen.getByRole("heading", { level: 3, name: "Injection" })).toBeInTheDocument();
    // INJ-VULN-01 块进卡片，不再渲染为 heading
    expect(screen.queryByRole("heading", { level: 3, name: /INJ-VULN-01/ })).not.toBeInTheDocument();
    const card = screen.getByTestId("vuln-card");
    expect(card).toHaveAttribute("data-severity");
    expect(card).toHaveTextContent("INJ-VULN-01");
  });

  it("TOC 含类型 + 执行摘要条目", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    const toc = container.querySelector('[data-testid="toc"]');
    expect(toc?.textContent).toContain("执行摘要");
    expect(toc?.textContent).toContain("Injection");
  });

  it("无 level>=2 标题时不渲染 TOC、外层退单栏", () => {
    const { container } = render(<MarkdownView markdown={"# 只有一级标题\n\n正文"} />);
    expect(container.querySelector('[data-testid="toc"]')).toBeNull();
    // 外层 grid 退单栏：无双栏 class
    expect(container.querySelector(".grid.grid-cols-\\[220px_1fr\\]")).toBeNull();
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

  it("键值字段渲染成 key-value 行（结构化 kv-row / kv-key / kv-val）", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    // 断言结构化处理存在：custom li 把 `- **key:** value` 拆成 kv-row > (kv-key + kv-val)。
    // prose 化后用 data-testid="kv-row" 检测（旧 .kv-row class 已换 Tailwind utilities）。
    const kvRows = container.querySelectorAll('li[data-testid="kv-row"]');
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
    // 负向断言（守冒号守卫，防 I-1 回归）：fixture 中真正的 kv 项 = 5（目标/评估日期/
    // vulnerability_type/verdict/witness_payload，均以 `:` 或 `：` 结尾）。
    // 执行摘要编号列表 `1. **RCE**（INJ-01）：eval` 等 bold-led 但无冒号的项
    // 不应被重构成 kv-row。若无冒号守卫，此计数会变 7（多出 RCE/SSRF），断言失败。
    expect(kvRows.length).toBe(5);
    const kvKeys = Array.from(kvRows).map(
      (li) => li.querySelector(".kv-key")?.textContent ?? "",
    );
    expect(kvKeys).not.toContain("RCE");
    expect(kvKeys).not.toContain("SSRF");
  });

  it("vuln 块的 witness_payload 进卡片 PoC（折叠，展开后显示）", () => {
    render(<MarkdownView markdown={MD} />);
    const toggle = screen.getByTestId("poc-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(screen.getByTestId("poc-code").textContent).toContain("preTax=res.send(...)");
  });

  it("prose 段 block code 在 <pre> 内、带复制按钮 + 语言角标", () => {
    const { container } = render(<MarkdownView markdown={"```bash\nexit 0\n```\n"} />);
    const pre = container.querySelector('pre[data-testid="code-block"]');
    expect(pre).not.toBeNull();
    expect(pre?.querySelector(".copy-btn")).not.toBeNull();
    expect(container.querySelector('[data-testid="code-lang"]')?.textContent).toBe("bash");
  });

  it("inline code 无 pre 包装、无复制按钮", () => {
    const { container } = render(<MarkdownView markdown={"正文 `inline_x` 结尾"} />);
    const code = container.querySelector("code");
    expect(code).not.toBeNull();
    expect(code?.textContent).toBe("inline_x");
    expect(container.querySelector("pre")).toBeNull();
    expect(container.querySelector(".copy-btn")).toBeNull();
  });

  it("带语言标记的 block code 显语言角标", () => {
    const { container } = render(<MarkdownView markdown={"```bash\nexit 0\n```\n"} />);
    const lang = container.querySelector('[data-testid="code-lang"]');
    expect(lang?.textContent).toBe("bash");
  });

  it("执行摘要 hero 条目锚链接到正文对应漏洞（href=#<vulnId>）", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    const hero = container.querySelector('[data-testid="exec-summary-hero"]');
    expect(hero).not.toBeNull();
    // 每个 hero 条目的 vuln-ID 应包裹在 <a href="#INJ-01"> 等锚链接里（spec §3.2）
    const anchors = hero?.querySelectorAll("a[href^='#']");
    expect(anchors?.length).toBeGreaterThan(0);
    const hrefs = Array.from(anchors ?? []).map((a) => a.getAttribute("href"));
    expect(hrefs).toContain("#INJ-01");
    expect(hrefs).toContain("#SSRF-01");
    expect(hrefs).toContain("#INJ-04");
    // 锚文本含 vuln-ID（可点击）
    const firstAnchor = anchors?.[0];
    expect(firstAnchor?.textContent).toMatch(/INJ-01/);
  });

  it("无执行摘要时不渲染 hero（不写死结构）", () => {
    const { container } = render(<MarkdownView markdown={"# 报告\n\n正文"} />);
    expect(container.querySelector('[data-testid="exec-summary-hero"]')).toBeNull();
  });

  it("md-body 容器带 prose 类", () => {
    render(<MarkdownView markdown="# T" />);
    expect(document.querySelector(".prose")).toBeInTheDocument();
  });

  it("GFM 表格渲染成 <table>（依赖 remark-gfm）", () => {
    const md = `
| 类型 | 数量 |
|------|------|
| INJ  | 4    |
| XSS  | 2    |
`;
    const { container } = render(<MarkdownView markdown={md} />);
    expect(container.querySelector("table")).not.toBeNull();
    expect(container.querySelectorAll("th").length).toBeGreaterThanOrEqual(2);
    expect(container.querySelectorAll("td").length).toBeGreaterThanOrEqual(2);
  });
});
