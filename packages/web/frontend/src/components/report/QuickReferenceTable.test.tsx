import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import i18n from "@/i18n";
import { QuickReferenceTable } from "./QuickReferenceTable";
import type { QuickReferenceRow } from "@/api/types";

// ── fixture：对齐 core QuickReferenceRow schema（snake_case 直传）──

const rows: QuickReferenceRow[] = [
  {
    id: "XSS-VULN-01",
    title: "备忘录存储型 XSS",
    params: ["memo (body)"],
    endpoints: ["POST /memos (write, isLoggedIn)"],
    severity: "high",
    verification: "静态分析",
    confidence: "待复核",
  },
  {
    id: "INJ-VULN-02",
    title: "NoSQL 注入（黑盒实测）",
    params: ["preTax (body)", "userId (query)"],
    endpoints: ["POST /contributions", "GET /contributions/:userId"],
    severity: "critical",
    verification: "动态实测",
    confidence: "高",
  },
  {
    id: "INJ-VULN-03",
    title: "多参数注入（截断档）",
    params: ["a (body)", "b (body)", "c (body)", "d (body)", "e (body)"],
    endpoints: ["POST /multi"],
    severity: "medium",
    verification: "静态分析",
    confidence: "中",
  },
  {
    id: "AUTHZ-VULN-01",
    title: "低危越权（点线档）",
    params: ["uid (query)"],
    endpoints: ["GET /profile/:uid"],
    severity: "low",
    verification: "静态分析",
    confidence: "低",
  },
];

beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => i18n.changeLanguage("zh"));

describe("QuickReferenceTable（漏洞速查表节）", () => {
  it("渲染表格：行数 = quick_reference 数，列头齐（ID/标题/参数/接口/严重程度/验证/置信度）", () => {
    render(<QuickReferenceTable rows={rows} onLocate={() => {}} />);
    expect(screen.getByTestId("quick-reference")).toBeInTheDocument();
    expect(screen.getByText("漏洞速查表")).toBeInTheDocument();
    const trs = screen.getAllByTestId("quick-ref-row");
    expect(trs.length).toBe(4); // 2026-08-27 增补 low 档行（线型阶梯断言）
    for (const h of ["标题", "参数", "接口", "严重程度", "验证", "置信度"]) {
      expect(screen.getByText(h)).toBeInTheDocument();
    }
  });

  it("行内容：多 params/endpoints 逗号拼接，severity/verification/confidence 单元格可见", () => {
    render(<QuickReferenceTable rows={rows} onLocate={() => {}} />);
    const row1 = screen.getAllByTestId("quick-ref-row")[0];
    const row2 = screen.getAllByTestId("quick-ref-row")[1];
    expect(within(row2).getByText(/preTax \(body\), userId \(query\)/)).toBeInTheDocument();
    expect(within(row2).getByText(/POST \/contributions, GET \/contributions\/:userId/)).toBeInTheDocument();
    expect(within(row2).getByText("critical")).toBeInTheDocument();
    expect(within(row2).getByText("动态实测")).toBeInTheDocument();
    expect(within(row2).getByText("高")).toBeInTheDocument();
    expect(within(row1).getByText("待复核")).toBeInTheDocument();
  });

  it("行点击（ID button，键盘可达）→ onLocate(vuln_id) 跳转对应卡", () => {
    const onLocate = vi.fn();
    render(<QuickReferenceTable rows={rows} onLocate={onLocate} />);
    fireEvent.click(screen.getByTestId("quick-ref-jump-XSS-VULN-01"));
    expect(onLocate).toHaveBeenCalledWith("XSS-VULN-01");
    fireEvent.click(screen.getByTestId("quick-ref-jump-INJ-VULN-02"));
    expect(onLocate).toHaveBeenCalledWith("INJ-VULN-02");
  });

  it("rows 空 → 整节不渲染（渲染层跳空，不出空壳表）", () => {
    render(<QuickReferenceTable rows={[]} onLocate={() => {}} />);
    expect(screen.queryByTestId("quick-reference")).not.toBeInTheDocument();
  });

  // ── 2026-08-27 速查表 triage 优化（左缘色规/截断/语义色/a11y）──

  it("行左缘 severity 色规：与 VulnerabilityCard SEV_EDGE 同语言（critical 红 / high 橙 / medium 黄 / low 中性）", () => {
    render(<QuickReferenceTable rows={rows} onLocate={() => {}} />);
    const trs = screen.getAllByTestId("quick-ref-row");
    expect(trs[1].className).toContain("border-l-red"); // critical（INJ-VULN-02）
    expect(trs[0].className).toContain("border-l-orange"); // high（XSS-VULN-01）
    expect(trs[2].className).toContain("border-l-yellow"); // medium（INJ-VULN-03）
  });

  it("行左缘线型阶梯：medium 虚线 / low 点线（spec 2026-08-27 §2.3 形状通道跨主题兜底）", () => {
    render(<QuickReferenceTable rows={rows} onLocate={() => {}} />);
    const trs = screen.getAllByTestId("quick-ref-row");
    expect(trs[2].className).toContain("[border-left-style:dashed]"); // medium（INJ-VULN-03）
    expect(trs[3].className).toContain("[border-left-style:dotted]"); // low（AUTHZ-VULN-01）
  });

  it("params >3 截断（对齐 md _params_cell 口径）：显示前 3 + 等 N 个，title 悬停可见全量", () => {
    render(<QuickReferenceTable rows={rows} onLocate={() => {}} />);
    const cell = screen.getByTestId("quick-ref-params-INJ-VULN-03");
    expect(cell.textContent).toContain("a (body), b (body), c (body)");
    expect(cell.textContent).toContain("等 5 个"); // n=总数（对齐 md _params_cell 口径）
    expect(cell.textContent).not.toContain("d (body)");
    expect(cell).toHaveAttribute("title", "a (body), b (body), c (body), d (body), e (body)");
    // ≤3 不截断：INJ-VULN-02 两参数全量
    const cell2 = screen.getByTestId("quick-ref-params-INJ-VULN-02");
    expect(cell2.textContent).toContain("userId (query)");
    expect(cell2).not.toHaveAttribute("title");
  });

  it("验证列语义色：动态验证 → 绿（实锤信号提亮）；静态 → muted（对齐卡内 evidence 徽章语言）", () => {
    render(<QuickReferenceTable rows={rows} onLocate={() => {}} />);
    const dynamic = screen.getByTestId("quick-ref-verification-INJ-VULN-02");
    expect(dynamic.className).toContain("text-green");
    const staticCell = screen.getByTestId("quick-ref-verification-XSS-VULN-01");
    expect(staticCell.className).toContain("text-muted-foreground");
  });

  it("置信度列：待复核/未判定 → amber（QA 风险信号）；高中低 → 常规文本", () => {
    render(<QuickReferenceTable rows={rows} onLocate={() => {}} />);
    const review = screen.getByTestId("quick-ref-confidence-XSS-VULN-01");
    expect(review.className).toContain("text-amber");
    const normal = screen.getByTestId("quick-ref-confidence-INJ-VULN-02");
    expect(normal.className).not.toContain("text-amber");
  });

  it("ID 列不折行：truncate 锁单行（ID 是原子标识符 token，连字符是 CSS 断行点，auto layout 挤压会在 - 处意外断成两行），title 悬停兜底超长 ID", () => {
    render(<QuickReferenceTable rows={rows} onLocate={() => {}} />);
    const btn = screen.getByTestId("quick-ref-jump-XSS-VULN-01");
    expect(btn.className).toContain("truncate"); // 含 whitespace-nowrap：真实 ID 9–13 字符恒单行
    expect(btn).toHaveAttribute("title", "XSS-VULN-01"); // agent 自由提交不受控，超长 ID 截断时悬停全文（对齐 params 列语言）
  });

  it("端点列 break-words 替代 break-all：优先空格// 优雅断行，仅超长无空格 token（如 GitNexus 路径证据 app/routes/x.js:Fn:21:19 ≈421px）才硬断——break-all 会把常规端点断成 POST /contri+butions 碎片", () => {
    render(<QuickReferenceTable rows={rows} onLocate={() => {}} />);
    const row1 = screen.getAllByTestId("quick-ref-row")[0];
    const tds = row1.querySelectorAll("td");
    expect(tds[3].className).toContain("break-words");
    expect(tds[3].className).not.toContain("break-all");
    // 参数列同语言兜底：超长无空格 token 硬断而非撑表
    expect(tds[2].className).toContain("break-words");
  });

  it("验证/置信度列 nowrap：值域是 builder 枚举短语（已动态验证/待复核，4–6 字），折行即挤压信号——中文会逐字断成三行", () => {
    render(<QuickReferenceTable rows={rows} onLocate={() => {}} />);
    const tr = screen.getAllByTestId("quick-ref-row")[1];
    const tds = tr.querySelectorAll("td");
    expect(tds[5].className).toContain("whitespace-nowrap"); // 验证
    expect(tds[6].className).toContain("whitespace-nowrap"); // 置信度
  });

  it("a11y：列头 scope=col（屏幕阅读器列关联）", () => {
    render(<QuickReferenceTable rows={rows} onLocate={() => {}} />);
    for (const th of screen.getAllByRole("columnheader")) {
      expect(th).toHaveAttribute("scope", "col");
    }
  });
});

  it("验证列融合四态：已实证→绿 / 复验失败→红 / 中断未结论→amber / 未覆盖→muted（spec 2026-09-03）", () => {
    const fused = [
      { id: "V-1", title: "a", params: [], endpoints: [], severity: "high",
        verification: "已实证", confidence: "high" },
      { id: "V-2", title: "b", params: [], endpoints: [], severity: "high",
        verification: "复验失败", confidence: "high" },
      { id: "V-3", title: "c", params: [], endpoints: [], severity: "high",
        verification: "中断未结论", confidence: "high" },
      { id: "V-4", title: "d", params: [], endpoints: [], severity: "high",
        verification: "未覆盖", confidence: "high" },
      { id: "V-5", title: "e", params: [], endpoints: [], severity: "high",
        verification: "黑盒独有", confidence: "high" },
      // 旧值兼容：untested 映射未覆盖
      { id: "V-6", title: "f", params: [], endpoints: [], severity: "high",
        verification: "untested", confidence: "high" },
    ] as never[];
    render(<QuickReferenceTable rows={fused} onLocate={() => {}} />);
    expect(screen.getByTestId("quick-ref-verification-V-1").className).toContain("text-green");
    expect(screen.getByTestId("quick-ref-verification-V-2").className).toContain("text-red");
    expect(screen.getByTestId("quick-ref-verification-V-3").className).toContain("text-amber");
    expect(screen.getByTestId("quick-ref-verification-V-4").className).toContain("text-muted-foreground");
    expect(screen.getByTestId("quick-ref-verification-V-5").className).toContain("text-green");
    expect(screen.getByTestId("quick-ref-verification-V-6").className).toContain("text-muted-foreground");
  });
