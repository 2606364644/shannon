import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render } from "@testing-library/react";
import i18n from "@/i18n";
import { ThreatOverview } from "./ThreatOverview";
import type { ReportStats } from "@/lib/report-stats";

function makeStats(over: Partial<ReportStats> = {}): ReportStats {
  return {
    total: 36,
    typeAggs: [
      {
        prefix: "INJ", displayName: "Injection", count: 4,
        severityRange: { min: "High", max: "Critical" }, severityRangeLabel: "Critical ~ High",
        severityCounts: { Critical: 3, High: 1, Medium: 0, Low: 0 },
      },
    ],
    severityDist: { Critical: 4, High: 20, Medium: 10, Low: 2 },
    publicCount: 34,
    preAuthCount: 8,
    topRisks: [{ text: "服务端 RCE", vulnIds: ["INJ-VULN-01"] }],
    ...over,
  };
}

describe("ThreatOverview", () => {
  beforeEach(() => i18n.changeLanguage("zh"));
  afterEach(() => i18n.changeLanguage("zh"));

  it("渲染 total 大数字 + 类型数 + 公网/pre-auth", () => {
    const { container } = render(<ThreatOverview stats={makeStats()} />);
    expect(container.textContent).toContain("36");
    expect(container.textContent).toContain("1 类");
    expect(container.textContent).toContain("34");
    expect(container.textContent).toContain("8");
  });

  it("severity 堆叠条段数 = 非零档数；0 档不渲染段", () => {
    const { container } = render(<ThreatOverview stats={makeStats()} />);
    expect(container.querySelectorAll('[data-testid^="threat-seg-"]').length).toBe(4);
    const { container: c2 } = render(
      <ThreatOverview stats={makeStats({ severityDist: { Critical: 0, High: 1, Medium: 0, Low: 0 } })} />,
    );
    expect(c2.querySelectorAll('[data-testid^="threat-seg-"]').length).toBe(1);
  });

  it("堆叠条段 flexGrow = 该档 count", () => {
    const { container } = render(<ThreatOverview stats={makeStats()} />);
    const crit = container.querySelector('[data-testid="threat-seg-Critical"]') as HTMLElement;
    expect(crit.style.flexGrow).toBe("4");
  });

  it("图例 4 档全显（含 0 档）", () => {
    const { container } = render(<ThreatOverview stats={makeStats()} />);
    expect(container.querySelector('[data-testid="threat-legend-Critical"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="threat-legend-High"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="threat-legend-Medium"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="threat-legend-Low"]')).not.toBeNull();
  });

  it("Top3 渲染 vid chip + 描述文本", () => {
    const { container } = render(<ThreatOverview stats={makeStats()} />);
    const top = container.querySelector('[data-testid="threat-toprisk"]');
    expect(top?.textContent).toContain("INJ-VULN-01");
    expect(top?.textContent).toContain("服务端 RCE");
  });

  it("i18n: 切英文 chrome 文案为英文", () => {
    i18n.changeLanguage("en");
    const { container } = render(<ThreatOverview stats={makeStats()} />);
    expect(container.textContent).toContain("Confirmed vulns");
    expect(container.textContent).toContain("1 types");
    expect(container.textContent).toContain("Public reachable");
    expect(container.textContent).toContain("By severity distribution");
    expect(container.textContent).toContain("Priority fixes");
    // section aria-label 英文
    expect(container.querySelector('[data-testid="threat-overview"]')).toHaveAttribute("aria-label", "Threat overview");
  });
});
