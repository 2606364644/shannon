import { describe, it, expect, afterEach } from "vitest";
import { render } from "@testing-library/react";
import i18n from "@/i18n";
import { TypeSummaryCards } from "./TypeSummaryCards";
import type { TypeAgg } from "@/lib/report-stats";

function makeAgg(over: Partial<TypeAgg> = {}): TypeAgg {
  return {
    prefix: "INJ",
    displayName: "Injection",
    count: 4,
    severityRange: { min: "High", max: "Critical" },
    severityRangeLabel: "Critical ~ High",
    severityCounts: { Critical: 3, High: 1, Medium: 0, Low: 0 },
    ...over,
  };
}

describe("TypeSummaryCards", () => {
  it("空数组 → 不渲染 section", () => {
    const { container } = render(<TypeSummaryCards typeAggs={[]} />);
    expect(container.querySelector('[data-testid="type-summary-cards"]')).toBeNull();
  });

  it("渲染 N 张卡（data-prefix）+ count 大数字", () => {
    const { container } = render(
      <TypeSummaryCards typeAggs={[makeAgg(), makeAgg({ prefix: "XSS", displayName: "XSS", count: 13 })]} />,
    );
    const cards = container.querySelectorAll('[data-testid="type-card"]');
    expect(cards.length).toBe(2);
    expect(container.querySelector('[data-prefix="INJ"]')?.textContent).toContain("4");
    expect(container.querySelector('[data-prefix="XSS"]')?.textContent).toContain("13");
  });

  it("色条 class 含 max-severity 色（max=Critical → bg-red）", () => {
    const { container } = render(<TypeSummaryCards typeAggs={[makeAgg()]} />);
    const stripe = container.querySelector('[data-testid="type-card-stripe"]');
    expect(stripe?.className).toContain("bg-red");
  });

  it("max=High 时色条为 bg-orange", () => {
    const { container } = render(
      <TypeSummaryCards typeAggs={[makeAgg({ severityRange: { min: "Low", max: "High" } })]} />,
    );
    expect(container.querySelector('[data-testid="type-card-stripe"]')?.className).toContain("bg-orange");
  });

  it("severity range 文字着色（Critical→text-red，High→text-orange）", () => {
    const { container } = render(<TypeSummaryCards typeAggs={[makeAgg()]} />);
    const card = container.querySelector('[data-testid="type-card"]');
    expect(card?.innerHTML).toContain("text-red");
    expect(card?.innerHTML).toContain("text-orange");
  });

  it("findingsText 存在时渲染，缺失时不渲染", () => {
    const { container: withF } = render(<TypeSummaryCards typeAggs={[makeAgg({ findingsText: "3 条 RCE" })]} />);
    expect(withF.querySelector('[data-testid="type-card"]')?.textContent).toContain("3 条 RCE");
    const { container: noF } = render(<TypeSummaryCards typeAggs={[makeAgg()]} />);
    expect(noF.querySelector('[data-testid="type-card"]')?.textContent).not.toContain("3 条 RCE");
  });

  describe("i18n", () => {
    afterEach(() => i18n.changeLanguage("zh"));

    it("中文 aria-label", () => {
      i18n.changeLanguage("zh");
      const { container } = render(<TypeSummaryCards typeAggs={[makeAgg()]} />);
      expect(container.querySelector('[data-testid="type-summary-cards"]')).toHaveAttribute("aria-label", "按漏洞类型汇总");
    });

    it("切英文 aria-label", () => {
      i18n.changeLanguage("en");
      const { container } = render(<TypeSummaryCards typeAggs={[makeAgg()]} />);
      expect(container.querySelector('[data-testid="type-summary-cards"]')).toHaveAttribute("aria-label", "Summary by vulnerability type");
    });
  });
});
