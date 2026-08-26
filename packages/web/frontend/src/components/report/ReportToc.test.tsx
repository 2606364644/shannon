import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import i18n from "@/i18n";
import { ReportToc, REPORT_EXEC_SUMMARY_ID, REPORT_CHAINS_ID } from "./ReportToc";
import type { ReportData, ReportVulnerability } from "@/api/types";

// ── fixture：与 ReportView.test 同形（只喂目录所需字段的最小合法集）──

const vuln = (id: string, title: string, severity: string): ReportVulnerability => ({
  id,
  type: "xss",
  vulnerability_type: "Stored",
  title,
  severity,
  confidence: "high",
  cwe_id: "CWE-79",
  externally_exploitable: true,
  merge_source: "both",
  merged_from: [],
  narrative: { cause: "c", impact: "i", remediation: "r" },
  endpoints: [],
  affected_entries: [],
  dataflow_steps: [],
  poc: null,
  evidence: null,
  attack_chain_refs: [],
});

const data: ReportData = {
  schema_version: 1,
  scan: { id: "scan-1", track: "whitebox", repo: "NodeGoat" },
  executive_summary: {
    narrative: "n",
    risk_level: "极高",
    top_risks: [],
    remediation_order: "r",
  },
  stats: null,
  vulnerabilities: [vuln("XSS-VULN-01", "备忘录存储型 XSS", "high"), vuln("INJ-VULN-02", "NoSQL 注入", "critical")],
  attack_chains: [{ id: "CHAIN-1", narrative: "链叙事" }],
  qa: null,
};

/** 挂目标锚点元素（目录点击定位的落点）。 */
function mountAnchor(id: string) {
  const el = document.createElement("section");
  el.id = id;
  document.body.appendChild(el);
  return el;
}

describe("ReportToc — 报告目录（2026-08-26 结构化路径新增）", () => {
  let scrollTo: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    i18n.changeLanguage("zh");
    scrollTo = vi.fn();
    window.scrollTo = scrollTo as unknown as typeof window.scrollTo;
  });
  afterEach(() => {
    i18n.changeLanguage("zh");
    document.body.innerHTML = "";
  });

  it("分组镜像区块：执行摘要 + 漏洞 (N) + 攻击链 (N)，漏洞条目带 severity 点与标题小字", () => {
    const { container } = render(<ReportToc data={data} />);
    expect(container.textContent ?? "").toContain("漏洞 (2)");
    expect(container.textContent ?? "").toContain("攻击链 (1)");
    expect(container.querySelector(`[data-toc-id="${REPORT_EXEC_SUMMARY_ID}"]`)).toBeTruthy();
    const entry = container.querySelector('[data-toc-id="XSS-VULN-01"]');
    expect(entry?.getAttribute("data-severity")).toBe("high");
    expect(entry?.textContent ?? "").toContain("备忘录存储型 XSS");
    // severity 状态点：critical 条目用 --c-red（与卡 SEV_DOT 同源）
    const critEntry = container.querySelector('[data-toc-id="INJ-VULN-02"]');
    const dot = critEntry?.querySelector("span[style]") as HTMLElement | null;
    expect(dot?.getAttribute("style") ?? "").toContain("hsl(var(--c-red))");
  });

  it("点击漏洞条目 → focusAnchor 精准定位（scrollTo smooth）+ 目标卡描边闪烁 + 立即高亮", () => {
    const target = mountAnchor("XSS-VULN-01");
    const { container } = render(<ReportToc data={data} />);
    fireEvent.click(container.querySelector('[data-toc-id="XSS-VULN-01"]')!);
    expect(scrollTo).toHaveBeenCalledTimes(1);
    expect(scrollTo.mock.calls[0][0]).toEqual({ top: expect.any(Number), behavior: "smooth" });
    expect(target.classList.contains("dataflow-flash")).toBe(true);
    // 点击立即置 active（不等 scrollspy 回填）
    expect(
      container.querySelector('[data-toc-id="XSS-VULN-01"]')!.getAttribute("aria-current"),
    ).toBe("true");
  });

  it("点击攻击链分组条目 → 定位 report-chains 锚点", () => {
    const target = mountAnchor(REPORT_CHAINS_ID);
    const { container } = render(<ReportToc data={data} />);
    fireEvent.click(container.querySelector(`[data-toc-id="${REPORT_CHAINS_ID}"]`)!);
    expect(scrollTo).toHaveBeenCalled();
    expect(target.classList.contains("dataflow-flash")).toBe(true);
  });

  it("无执行摘要 / 无攻击链 → 对应条目与分组不渲染", () => {
    const { container } = render(
      <ReportToc data={{ ...data, executive_summary: null, attack_chains: [] }} />,
    );
    expect(container.querySelector(`[data-toc-id="${REPORT_EXEC_SUMMARY_ID}"]`)).toBeNull();
    expect(container.querySelector(`[data-toc-id="${REPORT_CHAINS_ID}"]`)).toBeNull();
    expect(container.textContent ?? "").not.toContain("攻击链");
  });

  it("jsdom 无 IntersectionObserver → 渲染与点击不受影响（scrollspy 跳过）", () => {
    const target = mountAnchor("INJ-VULN-02");
    const { container } = render(<ReportToc data={data} />);
    fireEvent.click(container.querySelector('[data-toc-id="INJ-VULN-02"]')!);
    expect(target.classList.contains("dataflow-flash")).toBe(true);
  });
});
