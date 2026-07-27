import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import i18n from "@/i18n";
import { ScanFilters, useScanFilters, type ScanFiltersValue } from "./ScanFilters";
import type { ScanSummary } from "@/api/types";

// jsdom navigator.language 默认 en，LanguageDetector 会把 i18n 切到 en；
// 断言依赖中文渲染（getByPlaceholderText(/搜索/)），逐测试钉回 zh。
beforeEach(() => i18n.changeLanguage("zh"));

const scans: ScanSummary[] = [
  { scan_id: "s1", scan_type: "whitebox", status: "running", created_at: 100, vuln_count: 1, is_running: true, workspace: "ws-a" },
  { scan_id: "s2", scan_type: "blackbox", status: "completed", created_at: 200, vuln_count: 2, is_running: false, workspace: "ws-b" },
  { scan_id: "s3", scan_type: "whitebox", status: "failed", created_at: 300, vuln_count: 3, is_running: false, workspace: "ws-a" },
];

describe("ScanFilters", () => {
  it("renders four filter controls", () => {
    const v: ScanFiltersValue = { status: "all", type: "all", keyword: "", time: "all" };
    render(<ScanFilters value={v} onChange={() => {}} />);
    expect(screen.getByPlaceholderText(/搜索/)).toBeInTheDocument();
    expect(screen.getAllByRole("combobox").length).toBeGreaterThanOrEqual(3); // status/type/time
  });

  it("keyword input calls onChange", () => {
    let v: ScanFiltersValue = { status: "all", type: "all", keyword: "", time: "all" };
    render(<ScanFilters value={v} onChange={(nv) => (v = nv)} />);
    fireEvent.change(screen.getByPlaceholderText(/搜索/), { target: { value: "ws-a" } });
    expect(v.keyword).toBe("ws-a");
  });
});

describe("useScanFilters", () => {
  function run(scans: ScanSummary[], v: ScanFiltersValue) {
    // 直接调 hook via a tiny harness
    let result: ScanSummary[] = [];
    function Harness() {
      const { filtered } = useScanFilters(scans, v);
      result = filtered;
      return null;
    }
    render(<Harness />);
    return result;
  }

  it("status filter", () => {
    expect(run(scans, { status: "running", type: "all", keyword: "", time: "all" }).length).toBe(1);
  });

  it("type filter", () => {
    expect(run(scans, { status: "all", type: "whitebox", keyword: "", time: "all" }).length).toBe(2);
  });

  it("keyword filter matches scan_id or workspace", () => {
    expect(run(scans, { status: "all", type: "all", keyword: "ws-a", time: "all" }).length).toBe(2);
    expect(run(scans, { status: "all", type: "all", keyword: "s2", time: "all" }).length).toBe(1);
  });

  it("returns all when all=all + empty keyword", () => {
    expect(run(scans, { status: "all", type: "all", keyword: "", time: "all" }).length).toBe(3);
  });
});
