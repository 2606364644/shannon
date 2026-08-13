import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import i18n from "@/i18n";
import { ScanProgressBadge } from "./ScanProgressBadge";
import type { ScanSummary } from "@/api/types";

/** 造一个 ScanSummary，over 覆盖关键字段。默认运行中白盒 42%。 */
function makeScan(over: Partial<ScanSummary> = {}): ScanSummary {
  return {
    scan_id: "s1",
    scan_type: "whitebox",
    status: "running",
    created_at: 1,
    vuln_count: 0,
    is_running: true,
    progress_pct: 42,
    ...over,
  } as ScanSummary;
}

beforeEach(() => { i18n.changeLanguage("zh"); });

describe("ScanProgressBadge", () => {
  it("终态(completed)不渲染进度徽标", () => {
    const { container } = render(
      <ScanProgressBadge scan={makeScan({ status: "completed", is_running: false, progress_pct: 100 })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("终态(failed)不渲染进度徽标", () => {
    const { container } = render(
      <ScanProgressBadge scan={makeScan({ status: "failed", is_running: false, progress_pct: 0 })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("纯白盒运行中显示百分比 + 进度条 + 白盒段标签", () => {
    render(<ScanProgressBadge scan={makeScan({ scan_type: "whitebox", progress_pct: 42 })} />);
    expect(screen.getByText("42%")).toBeInTheDocument();
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "42");
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
    expect(screen.getByText("白盒")).toBeInTheDocument();
  });

  it("纯白盒运行中有 currentPhase 时段标签带 phase", () => {
    render(<ScanProgressBadge scan={makeScan({ scan_type: "whitebox" })} currentPhase="recon" />);
    // 段标签整体 "白盒 · recon"（单文本节点），用正则分别确认两段都在
    expect(screen.getByText(/白盒/)).toBeInTheDocument();
    expect(screen.getByText(/recon/)).toBeInTheDocument();
  });

  it("纯黑盒运行中显示黑盒段标签 + 百分比", () => {
    render(<ScanProgressBadge scan={makeScan({ scan_type: "blackbox", progress_pct: 60 })} />);
    expect(screen.getByText("黑盒")).toBeInTheDocument();
    expect(screen.getByText("60%")).toBeInTheDocument();
  });

  it("组合扫描 bb_phase=pending 显示白盒扫描中", () => {
    render(<ScanProgressBadge scan={makeScan({ scan_type: "whitebox", combined: true, bb_phase: "pending", progress_pct: 30 })} />);
    expect(screen.getByText("白盒扫描中")).toBeInTheDocument();
    expect(screen.getByText("30%")).toBeInTheDocument();
  });

  it("组合扫描 bb_phase=running 显示黑盒扫描中", () => {
    render(<ScanProgressBadge scan={makeScan({ scan_type: "whitebox", combined: true, bb_phase: "running", progress_pct: 70 })} />);
    expect(screen.getByText("黑盒扫描中")).toBeInTheDocument();
    expect(screen.getByText("70%")).toBeInTheDocument();
  });

  it("组合扫描 bb_phase=precheck 显示预验证中", () => {
    render(<ScanProgressBadge scan={makeScan({ scan_type: "whitebox", combined: true, bb_phase: "precheck", progress_pct: 0 })} />);
    expect(screen.getByText("预验证中")).toBeInTheDocument();
  });

  it("百分比夹紧到 0-100（负数/超百归一）", () => {
    render(<ScanProgressBadge scan={makeScan({ progress_pct: 150 })} />);
    expect(screen.getByText("100%")).toBeInTheDocument();
  });
});
