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
];

beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => i18n.changeLanguage("zh"));

describe("QuickReferenceTable（漏洞速查表节）", () => {
  it("渲染表格：行数 = quick_reference 数，列头齐（ID/标题/参数/接口/严重程度/验证/置信度）", () => {
    render(<QuickReferenceTable rows={rows} onLocate={() => {}} />);
    expect(screen.getByTestId("quick-reference")).toBeInTheDocument();
    expect(screen.getByText("漏洞速查表")).toBeInTheDocument();
    const trs = screen.getAllByTestId("quick-ref-row");
    expect(trs.length).toBe(2);
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
});
