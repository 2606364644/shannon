import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, within, fireEvent } from "@testing-library/react";
import i18n from "@/i18n";
import { MarkdownView, extractVulnIds } from "./MarkdownView";

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

1. **RCE**（INJ-VULN-01）：eval
2. **SSRF**（SSRF-VULN-01）：IMDSv1
3. **NoSQL $where 注入**（INJ-VULN-04）：threshold

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

// jsdom navigator.language 默认 en,LanguageDetector 把 i18n 切到 en;
// chrome 断言依赖中文渲染,逐测试钉回 zh(同 StatusBadge.test 模式)。
beforeEach(() => i18n.changeLanguage("zh"));

describe("MarkdownView", () => {
  it("渲染 H1/H2 标题；vuln 块完整渲染（标题+字段都在，按 severity 着色）", () => {
    render(<MarkdownView markdown={MD} />);
    expect(screen.getByRole("heading", { level: 1, name: "安全评估报告" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "执行摘要" })).toBeInTheDocument();
    // prose 段的 ### 类型小节仍是 heading
    expect(screen.getByRole("heading", { level: 3, name: "Injection" })).toBeInTheDocument();
    // vuln 块完整渲染：ID 在常驻 header，字段在 body（不裁剪、不丢信息）
    const card = screen.getByTestId("vuln-card");
    expect(card).toHaveAttribute("data-severity");
    expect(card).toHaveTextContent("INJ-VULN-01");
    expect(card).toHaveTextContent("CommandInjection");
  });

  it("TOC 含类型 + 执行摘要条目（从 DOM 读真实 id）", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    const toc = container.querySelector('[data-testid="toc"]');
    expect(toc).not.toBeNull();
    expect(toc?.textContent).toContain("执行摘要");
    expect(toc?.textContent).toContain("Injection");
  });

  it("TOC 每个锚链接命中 DOM 内真实 id（点击可跳转）", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    const toc = container.querySelector('[data-testid="toc"]');
    expect(toc).not.toBeNull();
    const links = toc!.querySelectorAll("a[href^='#']");
    expect(links.length).toBeGreaterThan(0);
    for (const a of Array.from(links)) {
      const id = a.getAttribute("href")!.slice(1);
      // 核心：TOC href 必须对应 DOM 真实元素，否则点击无反应
      expect(container.querySelector(`[id="${id}"]`)).not.toBeNull();
    }
  });

  it("TOC 默认折叠；章节可展开/收起（chevron + 全部展开）", () => {
    const md = [
      "# 报告",
      "",
      "## 单点漏洞",
      "",
      "### INJ-VULN-01: SQLi",
      "",
      "- **vulnerability_type:** SQLi",
      "",
      "### INJ-VULN-02: XSS",
      "",
      "- **vulnerability_type:** XSS",
    ].join("\n");
    const { container } = render(<MarkdownView markdown={md} />);
    const toc = container.querySelector('[data-testid="toc"]')!;
    // 默认折叠：章节带 chevron 但子条目隐藏、箭头收起
    expect(toc.querySelectorAll('[data-testid="toc-toggle"]').length).toBeGreaterThan(0);
    expect(toc.querySelectorAll('[data-testid="toc-children"]')).toHaveLength(0);
    expect(toc.querySelector('[data-testid="toc-toggle"]')).toHaveAttribute("aria-expanded", "false");
    // 展开该章节 → 子条目出现、chevron 展开
    fireEvent.click(toc.querySelector('[data-testid="toc-toggle"]')!);
    expect(toc.querySelectorAll('[data-testid="toc-children"]')).toHaveLength(1);
    expect(toc.querySelector('[data-testid="toc-toggle"]')).toHaveAttribute("aria-expanded", "true");
    // 再次点击 → 收起
    fireEvent.click(toc.querySelector('[data-testid="toc-toggle"]')!);
    expect(toc.querySelectorAll('[data-testid="toc-children"]')).toHaveLength(0);
    // 全部展开 → 子条目恢复
    fireEvent.click(toc.querySelector('[data-testid="toc-toggle-all"]')!);
    expect(toc.querySelectorAll('[data-testid="toc-children"]')).toHaveLength(1);
  });

  it("重复同名 h2 → 唯一 DOM id 且 TOC 各自命中（juice-shop 结构）", () => {
    const md = [
      "# Injection Findings",
      "",
      "## Identified Vulnerabilities",
      "",
      "### INJ-VULN-01: SQLi",
      "",
      "- **vulnerability_type:** SQLi",
      "",
      "# XSS Findings",
      "",
      "## Identified Vulnerabilities",
      "",
      "### XSS-VULN-01: Stored",
      "",
      "- **vulnerability_type:** Stored",
      "",
    ].join("\n");
    const { container } = render(<MarkdownView markdown={md} />);
    const toc = container.querySelector('[data-testid="toc"]');
    expect(toc).not.toBeNull();
    const ivLinks = Array.from(toc!.querySelectorAll("a")).filter((a) =>
      (a.textContent || "").includes("Identified Vulnerabilities"),
    );
    expect(ivLinks.length).toBe(2);
    const hrefs = ivLinks.map((a) => a.getAttribute("href"));
    // 两个同名 h2 的 href 必须不同（段级 slugger + 段前缀全局去重）
    expect(hrefs[0]).not.toBe(hrefs[1]);
    for (const href of hrefs) {
      const id = href!.slice(1);
      // 且每个 href 命中唯一 DOM id（无重复 id）
      expect(container.querySelectorAll(`[id="${id}"]`).length).toBe(1);
    }
  });

  it("章节不足 2 时不渲染 TOC、外层退单栏", () => {
    const { container } = render(<MarkdownView markdown={"# 只有一级标题\n\n正文"} />);
    expect(container.querySelector('[data-testid="toc"]')).toBeNull();
    // 外层 grid 退单栏：无双栏 class
    expect(container.querySelector(".grid.grid-cols-\\[200px_1fr\\]")).toBeNull();
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
    expect(vulnIdSpans?.[0].textContent).toBe("INJ-VULN-01");
    // 多 vuln-ID 用 / 连接（INJ-VULN-04 等），守 join 行为
    const inj04 = Array.from(vulnIdSpans ?? []).find((s) =>
      s.textContent?.includes("INJ-VULN-04"),
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

  it("Notes 段落降级为「注释 aside」（coral 左规 + eyebrow + 更小正文），不再按全尺寸 <p> 抢主发现权重", () => {
    // 后端 findings_renderer 把 notes 写成独立段落 `**备注:** <长文本>`（前导 \n 与 kv 列表分开），
    // 原本按全尺寸 prose <p> 渲染——又长又抢眼。改造后命中备注标签 → 注释 aside（视觉降级）。
    const md = `# 报告

## Injection

### INJ-VULN-01: eval RCE

- **verdict:** vulnerable

**备注:** affected_routes: ['GET /api/x', 'POST /api/y']; authentication_required: false. 这是一段很长的补充说明，含路由清单与置信度 caveat。
`;
    const { container } = render(<MarkdownView markdown={md} />);
    const notes = container.querySelector('[data-testid="vuln-notes"]');
    expect(notes).not.toBeNull();
    // eyebrow（源标签去冒号）+ 正文内容完整保留
    expect(notes?.textContent).toContain("备注");
    expect(notes?.textContent).toContain("affected_routes");
    expect(notes?.textContent).toContain("authentication_required: false");
    // 降级视觉权重的结构性证据：coral 左规 + 暖纸底 + 更小更柔的正文
    expect(notes).toHaveClass("border-primary/30");
    expect(notes).toHaveClass("bg-muted/40");
    const body = notes?.querySelector("div + div");
    expect(body).toHaveClass("text-foreground/70");
    expect(body).toHaveClass("text-[12.5px]");
    // 不再渲染成普通 <p>（旧的全尺寸段落）
    const card = screen.getByTestId("vuln-card");
    expect(card.querySelectorAll("p").length).toBe(0);
  });

  it("普通 bold-led 段落不被误判为 notes（仅捕获 备注/Notes 标签）", () => {
    const md = `# 报告

## Injection

### INJ-VULN-01: eval RCE

- **verdict:** vulnerable

**摘要:** 这是普通加粗段落，不应被降级为 notes aside。
`;
    const { container } = render(<MarkdownView markdown={md} />);
    expect(container.querySelector('[data-testid="vuln-notes"]')).toBeNull();
    // 仍是普通 <p>（未被重构成 aside）
    expect(container.querySelectorAll("p").length).toBeGreaterThan(0);
  });

  it("冗余的每类「已确认漏洞」h2 子标题降级为小标签（非 heading），并移出 TOC", () => {
    // 真实根因：report-executive prompt 指示 LLM 保留 REPORT_VULN_SUBHEADING（=「已确认漏洞」），
    // 它紧跟 `# <类> 漏洞利用报告` h1、漏洞卡片之前，与 h1 语义重复。降级为 <p> 小标签后：
    // 不再是 heading、不进 TOC（TOC 只收 h1/h2 DOM）。
    const md = `# 安全评估报告

## 执行摘要

正文。

# Injection 漏洞利用报告

## 已确认漏洞

### INJ-VULN-01: eval RCE

- **verdict:** vulnerable
`;
    const { container } = render(<MarkdownView markdown={md} />);

    // 不再是 h2 标题
    expect(screen.queryByRole("heading", { level: 2, name: "已确认漏洞" })).toBeNull();
    // 降级为 <p> 小标签
    const label = container.querySelector('[data-testid="vuln-subheading"]');
    expect(label?.tagName).toBe("P");
    expect(label?.textContent).toContain("已确认漏洞");
    expect(label).toHaveClass("font-semibold");
    // 合法 h2（执行摘要）不受影响，仍是 heading
    expect(screen.getByRole("heading", { level: 2, name: "执行摘要" })).toBeInTheDocument();
    // TOC 不含「已确认漏洞」
    const toc = container.querySelector('[data-testid="toc"]');
    expect(toc?.textContent ?? "").not.toContain("已确认漏洞");
  });

  it("英文「Confirmed Vulnerabilities」同样降级", () => {
    const md = `# Report

## Executive Summary

body.

# Injection Exploitation Report

## Confirmed Vulnerabilities

### INJ-VULN-01: eval RCE

- **verdict:** vulnerable
`;
    const { container } = render(<MarkdownView markdown={md} />);
    expect(screen.queryByRole("heading", { level: 2, name: "Confirmed Vulnerabilities" })).toBeNull();
    expect(container.querySelector('[data-testid="vuln-subheading"]')?.textContent).toContain("Confirmed");
  });

  it("末条 notes 紧跟 `---` 被 Setext 解析成 <h2> 也命中 aside（综合报告真实结构）", () => {
    // 真实根因：综合报告用 `---` 分隔漏洞条目，每类「最后一条」漏洞的 notes 行紧跟 `---`（无空行），
    // markdown 按 Setext 把 `**Notes:** 文本\n---` 解析成 <h2> 标题（又大又粗，且不走 <p> 通路）。
    // 工厂同时覆盖 <p> 与 <h1>~<h6> → 两条路都降级为 aside。
    const md = `# 报告

## Cross-Site Scripting (XSS)

### XSS-VULN-02

**Summary:**
- **Vulnerable Location:** startDate on /api/report-relation/logs
- **Verdict:** vulnerable

**Notes:** 认证要求：所有 .regex() 验证路由均有认证中间件。无 CSP 头，XSS payload 无约束。
---

## Authentication Vulnerabilities

### AUTH-VULN-01

**Summary:**
- **Source Endpoint:** ALL /api/*
`;
    const { container } = render(<MarkdownView markdown={md} />);
    const notes = container.querySelector('[data-testid="vuln-notes"]');
    expect(notes).not.toBeNull();
    expect(notes?.textContent).toContain("认证要求");
    // 关键回归断言：notes 不再残留为 <h2> 标题（Setext 副作用）
    const h2Notes = Array.from(container.querySelectorAll("h2")).some((h) =>
      /认证要求/.test(h.textContent ?? ""),
    );
    expect(h2Notes).toBe(false);
  });

  it("vuln 块的 witness_payload 代码完整展示（直接渲染，不折叠丢信息）", () => {
    render(<MarkdownView markdown={MD} />);
    const card = screen.getByTestId("vuln-card");
    // witness 代码内容直接出现在卡片里（完整展示，不再折叠成 PoC toggle）
    expect(card).toHaveTextContent("preTax=res.send(...)");
  });

  it("单张 vuln 卡片可独立折叠/展开（header 按钮 + aria-expanded）", () => {
    render(<MarkdownView markdown={MD} />);
    const card = screen.getByTestId("vuln-card");
    const toggle = within(card).getByTestId("vuln-toggle");
    // 默认展开：正文在、aria-expanded=true
    expect(card).toHaveTextContent("preTax=res.send(...)");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    // 折叠本卡 → 正文隐藏
    fireEvent.click(toggle);
    expect(within(card).getByTestId("vuln-toggle")).toHaveAttribute("aria-expanded", "false");
    expect(card).not.toHaveTextContent("preTax=res.send(...)");
    // 再展开 → 正文回来
    fireEvent.click(within(card).getByTestId("vuln-toggle"));
    expect(card).toHaveTextContent("preTax=res.send(...)");
  });

  it("prose 段 block code 在 <pre> 内、带复制按钮 + 语言角标", () => {
    const { container } = render(<MarkdownView markdown={"```bash\nexit 0\n```\n"} />);
    const pre = container.querySelector('pre[data-testid="code-block"]');
    expect(pre).not.toBeNull();
    expect(pre?.querySelector(".copy-btn")).not.toBeNull();
    expect(container.querySelector('[data-testid="code-lang"]')?.textContent).toBe("bash");
  });

  it("语言角标与复制按钮并排同一工具栏（矮代码块不垂直重叠）", () => {
    const { container } = render(<MarkdownView markdown={"```bash\nexit 0\n```\n"} />);
    const lang = container.querySelector('[data-testid="code-lang"]');
    const copy = container.querySelector(".copy-btn");
    expect(lang).not.toBeNull();
    expect(copy).not.toBeNull();
    // 二者为兄弟节点（同一 flex 工具栏内水平排列），而非一上一下绝对定位 →
    // 单行 http/bash 矮代码块也不再垂直重叠（用户报告的重叠 bug 回归守护）。
    expect(lang?.parentElement).toBe(copy?.parentElement);
  });

  it("inline code 无 pre 包装、无复制按钮", () => {
    const { container } = render(<MarkdownView markdown={"正文 `inline_x` 结尾"} />);
    const code = container.querySelector("code");
    expect(code).not.toBeNull();
    expect(code?.textContent).toBe("inline_x");
    expect(container.querySelector("pre")).toBeNull();
    expect(container.querySelector(".copy-btn")).toBeNull();
  });

  it("黑盒利用步骤嵌套命令围栏：归入对应步骤列表项、带复制按钮（步骤编号不丢）", () => {
    // 后端 renderers/exploit.py 尾随命令拆分产物（真实 AUTH-VULN-04 形态）：
    // 步骤有序列表 + 项内嵌套 ```bash 围栏 + 证据字段的终端转录围栏。
    const md = `# 认证利用报告

### AUTH-VULN-04: auth bypass
- **严重程度:** critical
- **利用步骤:**
  1. Send a pre-auth request to POST http://x/login.
  2. Observe the response: HTTP/1.1 302 Found.
  3. Follow the redirect with the issued cookie:
     \`\`\`bash
     curl -b 'connect.sid=<SID>' http://10.2.22.187:4000/benefits
     \`\`\`
- **影响证据:**
  \`\`\`bash
  curl -s -D - -o /dev/null -X POST http://x/login
  \`\`\`
`;
    const { container } = render(<MarkdownView markdown={md} />);
    // 两处围栏都渲染为带复制按钮的代码块（bash 角标）
    const blocks = container.querySelectorAll('pre[data-testid="code-block"]');
    expect(blocks.length).toBe(2);
    blocks.forEach((b) => expect(b.querySelector(".copy-btn")).not.toBeNull());
    // 命令完整落在代码块内（复制按钮拷到纯命令，$ 提示符后端已剥）
    expect(blocks[0]).toHaveTextContent("curl -b 'connect.sid=<SID>' http://10.2.22.187:4000/benefits");
    // 有序步骤仍是列表（fence 嵌套项内不断列表、编号文本保留）
    const ol = container.querySelector("ol");
    expect(ol).not.toBeNull();
    expect(ol?.querySelectorAll("li").length).toBe(3);
    expect(ol).toHaveTextContent("Follow the redirect with the issued cookie:");
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
    expect(hrefs).toContain("#INJ-VULN-01");
    expect(hrefs).toContain("#SSRF-VULN-01");
    expect(hrefs).toContain("#INJ-VULN-04");
    // 锚文本含 vuln-ID（可点击）
    const firstAnchor = anchors?.[0];
    expect(firstAnchor?.textContent).toMatch(/INJ-VULN-01/);
  });

  it("执行摘要 hero 的 GN 漏洞 ID 也提锚链接（GitNexus 轨 -GN- 进 topRiskIds 联动）", () => {
    const md = [
      "# 安全评估报告",
      "",
      "## 执行摘要",
      "",
      "1. **存储型 XSS**（XSS-GN-01）：profile 投毒",
      "2. **垂直越权**（AUTHZ-GN-EXPLORE-01）：userId 越权",
      "",
      "## XSS",
      "",
      "### XSS-GN-01: 存储型 XSS",
      "- **vulnerability_type:** Stored XSS",
      "",
      "## Authz",
      "",
      "### AUTHZ-GN-EXPLORE-01: 垂直越权",
      "- **vulnerability_type:** Vertical",
    ].join("\n");
    const { container } = render(<MarkdownView markdown={md} />);
    const hero = container.querySelector('[data-testid="exec-summary-hero"]');
    expect(hero).not.toBeNull();
    const hrefs = Array.from(hero?.querySelectorAll("a[href^='#']") ?? []).map((a) =>
      a.getAttribute("href"),
    );
    expect(hrefs).toContain("#XSS-GN-01");
    expect(hrefs).toContain("#AUTHZ-GN-EXPLORE-01");
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

describe("extractVulnIds（双轨 ID 口径，与 vuln-block VULN_HEADING_RE/VULN_ID_RE 对齐）", () => {
  it("LLM 轨 -VULN-：单 ID + /NN 展开（回归守卫）", () => {
    expect(extractVulnIds("**RCE**（INJ-VULN-01）：eval")).toEqual(["INJ-VULN-01"]);
    expect(extractVulnIds("**注入**（INJ-VULN-01/02/03）")).toEqual([
      "INJ-VULN-01",
      "INJ-VULN-02",
      "INJ-VULN-03",
    ]);
  });

  it("GitNexus 轨 -GN-：XSS-GN-01 / XSS-GN-13 / 多段 AUTHZ-GN-EXPLORE-01、AUTH-GN-LOGIC-01", () => {
    expect(extractVulnIds("**XSS**（XSS-GN-01）：反射")).toEqual(["XSS-GN-01"]);
    expect(extractVulnIds("**XSS**（XSS-GN-13）")).toEqual(["XSS-GN-13"]);
    expect(extractVulnIds("**越权**（AUTHZ-GN-EXPLORE-01）")).toEqual(["AUTHZ-GN-EXPLORE-01"]);
    expect(extractVulnIds("**认证**（AUTH-GN-LOGIC-01）")).toEqual(["AUTH-GN-LOGIC-01"]);
  });

  it("GN /NN 展开复用完整 stem（含 -GN-EXPLORE 中段）", () => {
    expect(extractVulnIds("（XSS-GN-01/03）")).toEqual(["XSS-GN-01", "XSS-GN-03"]);
    expect(extractVulnIds("（AUTHZ-GN-EXPLORE-01/02）")).toEqual([
      "AUTHZ-GN-EXPLORE-01",
      "AUTHZ-GN-EXPLORE-02",
    ]);
  });

  it("非漏洞 ID 形态不误报——对齐 VULN_ID_RE（须有 -<大写中段>-）", () => {
    expect(extractVulnIds("INJ-01")).toEqual([]);
    expect(extractVulnIds("llm-chain-1 是攻击链，非漏洞 ID")).toEqual([]);
  });
});

describe("MarkdownView 攻击链独立章节", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("攻击链章节独立渲染：单点漏洞进卡片，攻击链进独立 section + 计数分开", () => {
    const md = [
      "# 安全评估报告",
      "",
      "## 执行摘要",
      "",
      "1. **RCE**（INJ-VULN-01）：eval",
      "",
      "## 攻击链（多步利用路径）",
      "",
      "### llm-chain-1: XSS -> 劫持",
      "- **类型:** xss",
      "",
      "### llm-chain-2: SSRF",
      "- **类型:** ssrf",
      "",
      "## Injection",
      "",
      "### INJ-VULN-01: eval RCE",
      "- **vulnerability_type:** CommandInjection",
    ].join("\n");
    render(<MarkdownView markdown={md} />);
    // 单点漏洞卡片（INJ-VULN-01）
    const card = screen.getByTestId("vuln-card");
    expect(card).toHaveTextContent("INJ-VULN-01");
    // 攻击链独立 section + 计数
    const acSection = screen.getByTestId("attack-chain-section");
    expect(acSection).toHaveTextContent("llm-chain-1");
    expect(acSection).toHaveTextContent("llm-chain-2");
    expect(screen.getByTestId("attack-chain-count").textContent).toContain("2");
    // llm-chain 不当 vuln：攻击链内容不进单漏洞卡片
    expect(card).not.toHaveTextContent("llm-chain");
    // ThreatOverview 计数分开：单点漏洞 1 + 攻击链 2
    const overview = screen.getByTestId("threat-overview");
    expect(overview.textContent).toContain("单点漏洞");
    expect(overview.textContent).toContain("攻击链");
  });

  it("无攻击链章节时不渲染攻击链 section（老报告兼容）", () => {
    render(<MarkdownView markdown={MD} />);
    expect(screen.queryByTestId("attack-chain-section")).toBeNull();
    // ThreatOverview 不显示攻击链计数
    expect(screen.getByTestId("threat-overview").textContent).not.toContain("攻击链");
  });
});

describe("MarkdownView 中文「数量:」类型汇总", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("中文「数量:」类型汇总 → TypeSummaryCards 渲染全 5 类（含 0 计数卡）", () => {
    // 重现 hr_20260713-104726 现场现象：report-executive 中文 narration 把类型汇总写成
    // 「### Injection / - 数量: N 个」→ 旧 Count 正则不认「数量」→ typeSummaries 全 prefix=""
    // → 零计数补全跳过 → TypeSummaryCards 只剩由单点 blocks 驱动的 Auth 一张。
    // 修法：Count 正则兼容中文 + computeStats 用 displayName 反查补 prefix。
    const md = [
      "# 安全评估报告",
      "## 执行摘要",
      "## 按漏洞类型汇总",
      "### Injection",
      "- 数量: 2 个",
      "### XSS",
      "- 数量: 1 个",
      "### Auth",
      "- 数量: 5 个",
      "### Authz",
      "- 数量: 11 个",
      "### SSRF",
      "- 数量: 1 个",
      "## Authentication Vulnerabilities",
      "### AUTH-VULN-01: 弱密码",
      "### AUTH-VULN-02: 无 MFA",
      "### AUTH-VULN-03: 默认凭据",
      "### AUTH-VULN-04: 会话固定",
      "### AUTH-VULN-05: 注销失效",
    ].join("\n");
    render(<MarkdownView markdown={md} />);
    const cards = screen.getAllByTestId("type-card");
    expect(cards).toHaveLength(5);
    expect(within(cards[0]).getByText("Injection")).toBeInTheDocument();
    // 数量来自 blocks（5 个 AUTH 卡片），非 prose「数量:」（口径由 P1 保证 prose 不再虚高）
    const authCard = cards.find((c) => c.getAttribute("data-prefix") === "AUTH")!;
    expect(authCard).toBeDefined();
    expect(within(authCard).getByText("5")).toBeInTheDocument();
  });
});

describe("MarkdownView i18n", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("zh 渲染中文 chrome(目录 / 最高风险 / 复制)", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    // TOC 标签 + aria
    const toc = container.querySelector('[data-testid="toc"]');
    expect(toc?.textContent).toContain("目录");
    expect(toc?.getAttribute("aria-label")).toBe("目录");
    // hero 标题 chrome(非报告正文)
    expect(container.querySelector('[data-testid="exec-summary-hero"]')?.textContent).toContain("最高风险发现");
    // prose block code 复制按钮
    const { container: c2 } = render(<MarkdownView markdown={"```bash\nexit 0\n```\n"} />);
    expect(c2.querySelector(".copy-btn")?.textContent).toBe("复制");
  });

  it("切英文渲染英文 chrome(Contents / Top risk findings / Copy)", () => {
    i18n.changeLanguage("en");
    const { container } = render(<MarkdownView markdown={MD} />);
    const toc = container.querySelector('[data-testid="toc"]');
    expect(toc?.textContent).toContain("Contents");
    expect(toc?.getAttribute("aria-label")).toBe("Table of contents");
    expect(container.querySelector('[data-testid="exec-summary-hero"]')?.textContent).toContain("Top risk findings");
    const { container: c2 } = render(<MarkdownView markdown={"```bash\nexit 0\n```\n"} />);
    expect(c2.querySelector(".copy-btn")?.textContent).toBe("Copy");
  });

  it("报告 Markdown 正文不随语言切换(仍是中文 fixture)", () => {
    i18n.changeLanguage("en");
    render(<MarkdownView markdown={MD} />);
    // 渲染的报告标题/执行摘要仍是 fixture 中文(LLM 数据,不翻译)
    expect(screen.getByRole("heading", { level: 1, name: "安全评估报告" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "执行摘要" })).toBeInTheDocument();
  });
});

describe("MarkdownView sticky top 对齐全局栈（集成约束）", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("findings 浮动条已移除；两折叠按钮在 TOC 顶部行（目录树之前，展开后仍可见）", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    // findings-bar 浮动条不再存在
    expect(container.querySelector('[data-testid="findings-bar"]')).toBeNull();
    const toc = container.querySelector('[data-testid="toc"]');
    // 卡片「收起卡片」+ 目录「收起目录」两按钮都在 TOC 侧栏内
    const vulnExpandAll = container.querySelector('[data-testid="vuln-expand-all"]');
    const tocToggleAll = container.querySelector('[data-testid="toc-toggle-all"]');
    expect(vulnExpandAll).not.toBeNull();
    expect(tocToggleAll).not.toBeNull();
    expect(toc?.contains(vulnExpandAll)).toBe(true);
    expect(toc?.contains(tocToggleAll)).toBe(true);
    // ★ 两按钮都在目录树 <ul> 之前（DOM 顺序）：展开任意章节、目录条目增多时
    //   按钮恒在顶部可见，不被目录滚动区带走（用户 2026-07-24 诉求）。
    const ul = toc!.querySelector("ul");
    expect(ul).not.toBeNull();
    expect(vulnExpandAll!.compareDocumentPosition(ul!) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(tocToggleAll!.compareDocumentPosition(ul!) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it("TOC sticky top-20（旧 top-4 已改），不再贴视口顶被 chrome 盖", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    const toc = container.querySelector('[data-testid="toc"]');
    expect(toc).not.toBeNull();
    expect(toc?.className).toContain("sticky");
    expect(toc?.className).toContain("top-20");
    expect(toc?.className).not.toContain("top-4"); // 旧值已改
  });
});

describe("MarkdownView PoC 并入漏洞卡片（spec 2026-07-24）", () => {
  beforeEach(() => i18n.changeLanguage("zh"));
  // jsdom 无原生 scrollIntoView；TOC 不 focus 测试 mock 它，测后还原避免污染其他用例
  type ScrollFn = (...a: unknown[]) => void;
  const proto = Element.prototype as unknown as { scrollIntoView?: ScrollFn };
  let origScrollIntoView: ScrollFn | undefined;
  beforeEach(() => {
    origScrollIntoView = proto.scrollIntoView;
  });
  afterEach(() => {
    proto.scrollIntoView = origScrollIntoView;
  });

  /** 后端 report endpoint 拼接形态：主报告 + \n\n---\n\n + PoC md */
  function withPoc(pocMd: string, main = "# 报告\n\n## Injection\n\n### INJ-VULN-01: SQLi\n\n- **vulnerability_type:** SQLi\n") {
    return `${main}\n\n---\n\n${pocMd}`;
  }

  it("PoC 按 ID 并入对应卡片 body；独立 PoC 章节（含概览表）不再渲染", () => {
    const pocMd = [
      "# 可利用漏洞 PoC 集合（白盒）",
      "",
      "## 概览",
      "",
      "| ID | 类型 | 路径 | 认证 | 置信度 |",
      "| INJ-VULN-01 | injection | GET /login | 需登录 | ✓ |",
      "",
      "## 详细 PoC",
      "",
      "### ✓ INJ-VULN-01 · injection @ GET /login",
      "**置信度：已确认可复现**",
      "",
      "**curl:**",
      "```bash",
      "curl -i -X GET 'https://t/login?u=%27'",
      "```",
    ].join("\n");
    const { container } = render(<MarkdownView markdown={withPoc(pocMd)} />);
    const card = screen.getByTestId("vuln-card");
    // PoC curl 内容并入对应卡片
    expect(card).toHaveTextContent("curl -i -X GET");
    expect(card.querySelector('[data-testid="vuln-poc"]')).not.toBeNull();
    // 独立 PoC 章节完全移除（标题 + 概览表 都不在 DOM）
    expect(container.textContent).not.toContain("可利用漏洞 PoC 集合");
    expect(container.textContent).not.toContain("概览");
  });

  it("PoC 无对应漏洞卡片 → 末尾兜底（poc-orphan），不丢信息；主卡片不受污染", () => {
    const pocMd = [
      "# 可利用漏洞 PoC 集合（白盒）",
      "",
      "## 详细 PoC",
      "",
      "### ✓ ORPHAN-VULN-99 · injection @ GET /x",
      "**curl:**",
      "```bash",
      "curl -i -X GET 'https://t/x'",
      "```",
    ].join("\n");
    const { container } = render(<MarkdownView markdown={withPoc(pocMd)} />);
    const orphans = container.querySelectorAll('[data-testid="poc-orphan"]');
    expect(orphans).toHaveLength(1);
    expect(orphans[0]).toHaveTextContent("ORPHAN-VULN-99");
    expect(orphans[0]).toHaveTextContent("curl -i -X GET");
    // 主卡片不含 orphan PoC
    expect(screen.getByTestId("vuln-card")).not.toHaveTextContent("ORPHAN-VULN-99");
  });

  it("无 PoC 章节 → 不渲染 PoC 区块 / orphan（老报告兼容）", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    expect(container.querySelector('[data-testid="vuln-poc"]')).toBeNull();
    expect(container.querySelector('[data-testid="poc-orphan"]')).toBeNull();
  });

  it("TOC 锚点点击走 JS scrollIntoView（不依赖原生锚点 focus）", () => {
    const scrollIntoView = vi.fn();
    proto.scrollIntoView = scrollIntoView;
    const { container } = render(<MarkdownView markdown={MD} />);
    const toc = container.querySelector('[data-testid="toc"]');
    const link = toc!.querySelector("a[href^='#']") as HTMLAnchorElement;
    expect(link).toBeTruthy();
    fireEvent.click(link);
    // onClick preventDefault + 手动 scrollIntoView：证明走 JS 路径而非浏览器原生锚点 focus
    expect(scrollIntoView).toHaveBeenCalled();
  });
});
