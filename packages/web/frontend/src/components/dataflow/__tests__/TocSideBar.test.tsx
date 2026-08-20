import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, fireEvent, act } from "@testing-library/react";
import i18n from "@/i18n";
import { TocSideBar, focusDataflowAnchor } from "../TocSideBar";
import type { DataflowTree, ControlFinding, SafeVector } from "@/api/types";

// —— fixture（对齐 PruningTreeFig.test 惯例）——

const vulnTree: DataflowTree = {
  tree_id: "T-VULN-01",
  vuln_class: "injection",
  sink: { label: "cursor.execute", file: "app/db.py", line: 42, rule_id: "py-sql-execute-raw", category: "sql", code: null },
  findings: [{ id: "INJ-VULN-01", merge_source: "both", title: "SQL 注入", confidence: "high" }],
  branches: [
    {
      branch_id: "F-01",
      track: "gitnexus",
      verdict: "vulnerable",
      verdict_reason: null,
      source: { label: "req.query.name", type: "query", entry: "GET /api/users", file: "r", line: 1 },
      nodes: [],
      sanitizers: [],
    },
  ],
};

const safeTree: DataflowTree = {
  ...vulnTree,
  tree_id: "T-SAFE-01",
  findings: [],
  branches: [
    {
      branch_id: "F-S-01",
      track: "gitnexus",
      verdict: "safe",
      verdict_reason: null,
      source: { label: "req.query.id", type: "query", entry: "GET /api/x", file: "r", line: 2 },
      nodes: [],
      sanitizers: [{ name: "shlex.quote", defense_type: "shlex_quote", file: "s.ts", line: 3, effective: true }],
    },
  ],
};

const control: ControlFinding = {
  id: "AUTHZ-IDOR-01",
  vuln_class: "authz",
  endpoint: "DELETE /api/users/:id",
  chain: [
    { label: "会话认证", status: "ok", detail: "auth middleware 覆盖", file: "app/mw.ts", line: 10 },
    { label: "owner 校验", status: "missing", detail: "缺少 owner 检查", file: "app/routes.ts", line: 88 },
  ],
};

const vectors: SafeVector[] = [
  { subject: "req.query.tag", location: "app/list.ts:12", defense_mechanism: "参数化查询" },
  { subject: "req.body.bio", location: "app/profile.ts:30", defense_mechanism: null },
];

// —— IntersectionObserver stub（jsdom 无实现，scrollspy 需手动喂 entry）——
class MockIO {
  static instances: MockIO[] = [];
  cb: IntersectionObserverCallback;
  observed: Element[] = [];
  constructor(cb: IntersectionObserverCallback) {
    this.cb = cb;
    MockIO.instances.push(this);
  }
  observe(el: Element) {
    this.observed.push(el);
  }
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}

describe("TocSideBar — 目录侧栏（spec §5）", () => {
  beforeEach(() => {
    i18n.changeLanguage("zh");
    MockIO.instances = [];
    vi.stubGlobal("IntersectionObserver", MockIO);
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
  });
  afterEach(() => {
    i18n.changeLanguage("zh");
    vi.unstubAllGlobals();
  });

  it("分组镜像三区：漏洞数据流树 (N) / 认证·授权风险 (N) / 排查过的入口 (N)", () => {
    const { container } = render(
      <TocSideBar trees={[vulnTree, safeTree]} controls={[control]} safeVectors={vectors} />,
    );
    expect(container.textContent ?? "").toContain("漏洞数据流树 (2)");
    expect(container.textContent ?? "").toContain("认证·授权风险 (1)");
    expect(container.textContent ?? "").toContain("排查过的入口 (2)");
  });

  it("树条目：●红=有打通枝 / ✂绿=全部剪断 + sink 名 + 次行 finding IDs · N打通/M剪断", () => {
    const { container } = render(
      <TocSideBar trees={[vulnTree, safeTree]} controls={[]} safeVectors={[]} />,
    );
    const vulnEntry = container.querySelector('[data-toc-id="T-VULN-01"]');
    expect(vulnEntry).toBeTruthy();
    expect(vulnEntry?.getAttribute("data-status")).toBe("vuln"); // ●红
    expect(vulnEntry?.textContent ?? "").toContain("cursor.execute");
    expect(vulnEntry?.textContent ?? "").toContain("INJ-VULN-01"); // finding IDs
    expect(vulnEntry?.textContent ?? "").toContain("1打通/0剪断"); // N打通/M剪断

    const safeEntry = container.querySelector('[data-toc-id="T-SAFE-01"]');
    expect(safeEntry?.getAttribute("data-status")).toBe("safe"); // ✂绿
    expect(safeEntry?.textContent ?? "").toContain("0打通/1剪断");
  });

  it("认证·授权条目：▲黄图标 + endpoint", () => {
    const { container } = render(
      <TocSideBar trees={[]} controls={[control]} safeVectors={[]} />,
    );
    const entry = container.querySelector('[data-toc-id="AUTHZ-IDOR-01"]');
    expect(entry).toBeTruthy();
    expect(entry?.getAttribute("data-status")).toBe("control");
    expect(entry?.textContent ?? "").toContain("DELETE /api/users/:id");
  });

  it("scrollspy：IntersectionObserver 命中的锚点对应条目高亮（aria-current）", async () => {
    const { container } = render(
      <div>
        {/* 右内容区锚点（PruningTreeFig 的树卡 data-tree-id） */}
        <div data-tree-id="T-VULN-01">tree1</div>
        <div data-tree-id="T-SAFE-01">tree2</div>
        <TocSideBar trees={[vulnTree, safeTree]} controls={[]} safeVectors={[]} />
      </div>,
    );
    const io = MockIO.instances[0];
    expect(io).toBeTruthy();
    // 观察了两个树锚点
    expect(io.observed.length).toBe(2);
    // 滚动到第二棵树 → 第二个条目高亮（IO 回调触发 setState，须包 act 等 commit）
    const target = container.querySelector('[data-tree-id="T-SAFE-01"]')!;
    await act(async () => {
      io.cb(
        [{ isIntersecting: true, target, boundingClientRect: {} } as unknown as IntersectionObserverEntry],
        io as unknown as IntersectionObserver,
      );
    });
    const active = container.querySelector('[data-toc-id="T-SAFE-01"]');
    expect(active?.getAttribute("aria-current")).toBe("true");
    const inactive = container.querySelector('[data-toc-id="T-VULN-01"]');
    expect(inactive?.getAttribute("aria-current")).toBeNull();
  });

  it("点击条目 → 平滑滚动到目标卡 + coral 描边闪烁（dataflow-flash）", () => {
    const scrollIntoView = vi.fn();
    window.HTMLElement.prototype.scrollIntoView = scrollIntoView;
    const { container } = render(
      <div>
        <div data-tree-id="T-VULN-01">tree1</div>
        <TocSideBar trees={[vulnTree]} controls={[]} safeVectors={[]} />
      </div>,
    );
    const entry = container.querySelector('[data-toc-id="T-VULN-01"]') as HTMLElement;
    fireEvent.click(entry);
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView.mock.calls[0][0]).toEqual({ behavior: "smooth", block: "start" });
    // 目标卡加上描边闪烁 class
    const target = container.querySelector('[data-tree-id="T-VULN-01"]')!;
    expect(target.classList.contains("dataflow-flash")).toBe(true);
  });

  it("点击排查过的入口分组头 → 滚动到 safe 区锚点", () => {
    const scrollIntoView = vi.fn();
    window.HTMLElement.prototype.scrollIntoView = scrollIntoView;
    const { container } = render(
      <div>
        <div data-safe-section="">safe</div>
        <TocSideBar trees={[]} controls={[]} safeVectors={vectors} />
      </div>,
    );
    const groupHead = container.querySelector('[data-toc-id="safe-entries"]') as HTMLElement;
    expect(groupHead).toBeTruthy();
    fireEvent.click(groupHead);
    expect(scrollIntoView).toHaveBeenCalled();
    expect(container.querySelector('[data-safe-section]')!.classList.contains("dataflow-flash")).toBe(true);
  });

  // —— Fix round 1 F①：coral 描边残留累积 ——
  it("连点多目标：旧目标描边被清除，新目标唯一闪烁（不残留累积）", () => {
    const { container } = render(
      <div>
        <div data-tree-id="T-VULN-01">a</div>
        <div data-tree-id="T-SAFE-01">b</div>
        <TocSideBar trees={[vulnTree, safeTree]} controls={[]} safeVectors={[]} />
      </div>,
    );
    fireEvent.click(container.querySelector('[data-toc-id="T-VULN-01"]')!);
    expect(
      container.querySelector('[data-tree-id="T-VULN-01"]')!.classList.contains("dataflow-flash"),
    ).toBe(true);
    fireEvent.click(container.querySelector('[data-toc-id="T-SAFE-01"]')!);
    // 新目标唯一闪烁
    expect(
      container.querySelector('[data-tree-id="T-SAFE-01"]')!.classList.contains("dataflow-flash"),
    ).toBe(true);
    // 旧目标描边已清除（单一 active-target 语义）
    expect(
      container.querySelector('[data-tree-id="T-VULN-01"]')!.classList.contains("dataflow-flash"),
    ).toBe(false);
    // 全文档最多一个带描边的目标
    expect(container.querySelectorAll(".dataflow-flash").length).toBe(1);
  });

  it("闪烁自动清理：动画时长对齐的 timer 后描边摘除（瞬时定位提示非持久状态）", () => {
    vi.useFakeTimers();
    try {
      const { container } = render(
        <div>
          <div data-tree-id="T-VULN-01">a</div>
          <TocSideBar trees={[vulnTree]} controls={[]} safeVectors={[]} />
        </div>,
      );
      fireEvent.click(container.querySelector('[data-toc-id="T-VULN-01"]')!);
      expect(
        container.querySelector('[data-tree-id="T-VULN-01"]')!.classList.contains("dataflow-flash"),
      ).toBe(true);
      vi.advanceTimersByTime(2100);
      expect(
        container.querySelector('[data-tree-id="T-VULN-01"]')!.classList.contains("dataflow-flash"),
      ).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("focusDataflowAnchor — ?tree= 定位（DataFlowTab / VulnCard 跳转共用）", () => {
  beforeEach(() => {
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
  });
  it("按 tree_id 定位：滚动 + 闪烁，找不到返回 false", () => {
    const el = document.createElement("section");
    el.setAttribute("data-tree-id", "T-X");
    document.body.appendChild(el);
    expect(focusDataflowAnchor("T-X")).toBe(true);
    expect(el.classList.contains("dataflow-flash")).toBe(true);
    expect(focusDataflowAnchor("T-NONE")).toBe(false);
    document.body.removeChild(el);
  });
});
