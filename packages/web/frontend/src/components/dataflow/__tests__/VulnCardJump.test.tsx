import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import i18n from "@/i18n";
import { VulnCard } from "../../VulnCard";
import { buildFindingTreeMap } from "../findingTreeMap";
import type { DataflowView, Vulnerability } from "@/api/types";

const base: Vulnerability = {
  ID: "INJ-VULN-01",
  vulnerability_type: "SQL_Injection",
  externally_exploitable: false,
};

const view: DataflowView = {
  schema_version: 1,
  summary: { total_sinks: 2, vulnerable_sinks: 1, safe_only_sinks: 1 },
  trees: [
    {
      tree_id: "T-VULN-01",
      vuln_class: "injection",
      sink: { label: "cursor.execute", file: "app/db.py", line: 42, rule_id: null, category: null, code: null },
      findings: [
        { id: "INJ-VULN-01", merge_source: "both", title: "SQL 注入", confidence: "high" },
        { id: "INJ-VULN-02", merge_source: "llm-only", title: null, confidence: null },
      ],
      branches: [],
    },
    {
      tree_id: "T-SAFE-01",
      vuln_class: "xss",
      sink: { label: "res.send", file: "app/x.ts", line: 9, rule_id: null, category: null, code: null },
      findings: [{ id: null, merge_source: null, title: null, confidence: null }],
      branches: [],
    },
  ],
  control_findings: [],
  safe_vectors: [],
};

function renderCard(v: Vulnerability, treeId: string | null | undefined) {
  return render(
    <MemoryRouter initialEntries={["/p/ws/scans/s1/deliverables"]}>
      <VulnCard v={v} dataflowTreeId={treeId} />
    </MemoryRouter>,
  );
}

describe("VulnCard — 「查看数据流」跳转（spec §5 路由与入口）", () => {
  beforeEach(() => i18n.changeLanguage("zh"));
  afterEach(() => i18n.changeLanguage("zh"));

  it("展开态 + 有 tree_id → 「查看数据流」链接，href 含 ?tree=", () => {
    renderCard(base, "T-VULN-01");
    // 初始收起：无链接
    expect(screen.queryByText(/查看数据流/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));
    const link = screen.getByText(/查看数据流/).closest("a");
    expect(link).toBeTruthy();
    expect(link?.getAttribute("href") ?? "").toContain("?tree=T-VULN-01");
    expect(link?.getAttribute("href") ?? "").toContain("dataflow");
  });

  it("无 tree_id（无映射 / auth 类不在树上）→ 不渲染链接", () => {
    renderCard(base, null);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByText(/查看数据流/)).not.toBeInTheDocument();
  });

  it("不传 prop（存量调用方）→ 不渲染链接，无 router 依赖", () => {
    // 不包 MemoryRouter：prop 缺省时 Link 不渲染 → 不需要 Router 上下文
    render(<VulnCard v={base} />);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByText(/查看数据流/)).not.toBeInTheDocument();
  });

  it("i18n：切英文链接文案 View data flow", () => {
    i18n.changeLanguage("en");
    renderCard(base, "T-VULN-01");
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText(/View data flow/)).toBeInTheDocument();
  });
});

describe("buildFindingTreeMap — finding_id → tree_id 映射（DeliverablesTab 传入）", () => {
  it("树上每个 finding id 都映射到其 tree_id", () => {
    const m = buildFindingTreeMap(view);
    expect(m.get("INJ-VULN-01")).toBe("T-VULN-01");
    expect(m.get("INJ-VULN-02")).toBe("T-VULN-01");
    expect(m.get("INJ-VULN-99")).toBeUndefined();
  });

  it("finding id 缺失的条目跳过（不产生 null key）", () => {
    const m = buildFindingTreeMap(view);
    expect(m.has("")).toBe(false);
  });

  it("空/未加载视图 → 空映射", () => {
    expect(buildFindingTreeMap(null).size).toBe(0);
    expect(buildFindingTreeMap(undefined).size).toBe(0);
    expect(
      buildFindingTreeMap({ ...view, trees: [] }).size,
    ).toBe(0);
  });
});
