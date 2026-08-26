import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { focusAnchor, stickyHeaderOffset } from "./focusAnchor";

// ── jsdom 无布局：getBoundingClientRect 全 0，逐用例注入假 rect；scrollTo/scrollY stub ──

function stubRect(el: Element, top: number, bottom: number) {
  el.getBoundingClientRect = () => ({ top, bottom }) as DOMRect;
}

function stubScrollY(y: number) {
  Object.defineProperty(window, "scrollY", { value: y, configurable: true });
}

/** 造一个带 id 的目标元素（+可选高度感 rect）。 */
function mountTarget(id: string, top = 1000): HTMLElement {
  const el = document.createElement("section");
  el.id = id;
  document.body.appendChild(el);
  stubRect(el, top, top + 400);
  return el;
}

/** 造 sticky 头元素（topbar / scan-sticky-header）。 */
function mountSticky(testid: string, bottom: number) {
  const el = document.createElement("div");
  el.setAttribute("data-testid", testid);
  document.body.appendChild(el);
  stubRect(el, 0, bottom);
  return el;
}

describe("stickyHeaderOffset — 运行时量 sticky 遮蔽带", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("TopBar + scan sticky 块都在 → 取 max(bottom) + 呼吸余量 8px", () => {
    mountSticky("topbar", 48);
    mountSticky("scan-sticky-header", 150);
    expect(stickyHeaderOffset()).toBe(158);
  });

  it("只有 TopBar（非 scan 页）→ topbar.bottom + 8", () => {
    mountSticky("topbar", 48);
    expect(stickyHeaderOffset()).toBe(56);
  });

  it("sticky 元素缺席 → 只留呼吸余量", () => {
    expect(stickyHeaderOffset()).toBe(8);
  });
});

describe("focusAnchor — 目录/锚点精准定位 + 闪烁反馈", () => {
  let scrollTo: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    scrollTo = vi.fn();
    window.scrollTo = scrollTo as unknown as typeof window.scrollTo;
    stubScrollY(0);
  });
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("目标缺席 → false 且不滚动不闪烁", () => {
    expect(focusAnchor("nope")).toBe(false);
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it("命中 → scrollTo top = el.top + scrollY − 遮蔽带（behavior smooth）+ 目标加 flash 描边", () => {
    mountSticky("topbar", 48);
    mountSticky("scan-sticky-header", 150);
    stubScrollY(300);
    const el = mountTarget("VULN-1", 1000);
    expect(focusAnchor("VULN-1")).toBe(true);
    // 1000 + 300 − 158 = 1142：卡片顶落在遮蔽带下沿 +8px，而非 scroll-mt-20 的 80px
    expect(scrollTo).toHaveBeenCalledWith({ top: 1142, behavior: "smooth" });
    expect(el.classList.contains("dataflow-flash")).toBe(true);
  });

  it("计算值为负 → 钳到 0（目标本就在页首）", () => {
    mountSticky("topbar", 48);
    mountTarget("VULN-2", 20);
    focusAnchor("VULN-2");
    expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: "smooth" });
  });

  it("单一 active-target：连点第二个目标，旧目标描边被摘除", () => {
    const a = mountTarget("VULN-A", 500);
    const b = mountTarget("VULN-B", 1500);
    focusAnchor("VULN-A");
    focusAnchor("VULN-B");
    expect(a.classList.contains("dataflow-flash")).toBe(false);
    expect(b.classList.contains("dataflow-flash")).toBe(true);
  });

  it("闪烁 2s 后自动清理（timer 兜底，jsdom 无动画事件）", () => {
    vi.useFakeTimers();
    try {
      const el = mountTarget("VULN-C", 900);
      focusAnchor("VULN-C");
      expect(el.classList.contains("dataflow-flash")).toBe(true);
      vi.advanceTimersByTime(2000);
      expect(el.classList.contains("dataflow-flash")).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("自定义 resolve（dataflow 的 data-tree-id 查找）→ 同样精准定位", () => {
    const el = document.createElement("div");
    el.setAttribute("data-tree-id", "T-1");
    document.body.appendChild(el);
    stubRect(el, 800, 1200);
    const resolve = (id: string) => document.querySelector(`[data-tree-id="${id}"]`);
    expect(focusAnchor("T-1", resolve)).toBe(true);
    expect(scrollTo).toHaveBeenCalledWith({ top: 792, behavior: "smooth" });
    expect(el.classList.contains("dataflow-flash")).toBe(true);
  });
});
