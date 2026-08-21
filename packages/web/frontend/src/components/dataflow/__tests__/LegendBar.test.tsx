import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import i18n from "@/i18n";
import { LegendBar } from "../LegendBar";

// 图例条（spec 2026-08-20 §5「汇总条与图例」：教读图）——Task 14。
// 5 类图例项对齐 §5 视觉语言表，样例复用剪枝树 CSS class 且静态不动画。

describe("LegendBar — 图例条（spec §5 视觉语言表）", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("渲染 6 类图例项：打通 / 剪断 / 黄盾 / 绿盾 / 靶心 / 同一函数弧（含白话文案）", () => {
    const { container } = render(<LegendBar />);
    const bar = container.querySelector('[data-testid="dataflow-legend-bar"]');
    expect(bar).toBeTruthy();
    for (const kind of ["vuln", "cut", "shield-bypass", "shield-effective", "target", "sameline"]) {
      expect(bar?.querySelector(`[data-legend="${kind}"]`)).toBeTruthy();
    }
    // 白话文案（spec §5 口径：打通/剪断/被绕过/有效/无输入到达/同一函数）
    expect(screen.getByText(/漏洞链路/)).toBeInTheDocument();
    expect(screen.getByText(/防护拦下/)).toBeInTheDocument();
    expect(screen.getByText(/防护被绕过/)).toBeInTheDocument();
    expect(screen.getByText(/有效防护（剪断点）/)).toBeInTheDocument();
    expect(screen.getByText(/有打通枝到达/)).toBeInTheDocument();
    expect(screen.getByText(/无输入到达/)).toBeInTheDocument();
    expect(screen.getByText(/同一函数/)).toBeInTheDocument();
    expect(screen.getByText("图例")).toBeInTheDocument();
  });

  it("样例复用 PruningTreeFig 的 CSS class（branch-vuln / branch-safe+✂+残端 / 黄绿盾 / 灰虚线靶心）", () => {
    const { container } = render(<LegendBar />);
    const cls = (sel: string) => container.querySelector(sel)?.getAttribute("class") ?? "";
    // 打通：红虚线（branch-vuln，无 .flow 动画组合类）
    expect(cls('[data-legend="vuln"] path')).toBe("branch-vuln");
    // 剪断：绿实线 + 防护节点 + ✂ + 渐隐残端
    expect(cls('[data-legend="cut"] .branch-safe')).toContain("branch-safe");
    expect(cls('[data-legend="cut"] .node-box-safe')).toContain("node-box-safe");
    expect(cls('[data-legend="cut"] .branch-remnant')).toContain("branch-remnant");
    expect(container.querySelector('[data-legend="cut"] [data-scissors]')).toBeTruthy();
    // 黄盾=被绕过 / 绿盾=有效（剪断点，带 ✂）
    expect(cls('[data-legend="shield-bypass"] circle')).toContain("shield-yellow");
    expect(cls('[data-legend="shield-effective"] circle')).toContain("shield-green");
    expect(container.querySelector('[data-legend="shield-effective"] [data-scissors]')).toBeTruthy();
    // 靶心一项双样例：红靶心 + 灰虚线靶心（sink-idle）
    expect(container.querySelector('[data-legend="target"] [data-sample="target-vuln"]')).toBeTruthy();
    expect(cls('[data-legend="target"] [data-sample="target-safe"]')).toContain("sink-idle");
    // 同一函数弧：青色点线（sameline；每弧文字标签已去——多弧时互叠，语义收进本图例）
    expect(cls('[data-legend="sameline"] path')).toContain("sameline");
  });

  it("静态不动画：图例条不含 .flow 流动 / .sink-pulse 脉动动画类", () => {
    const { container } = render(<LegendBar />);
    expect(container.querySelector(".flow")).toBeNull();
    expect(container.querySelector(".sink-pulse")).toBeNull();
  });
});
