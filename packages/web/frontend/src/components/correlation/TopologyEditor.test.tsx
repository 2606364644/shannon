import { fireEvent, render, screen } from "@testing-library/react";
import {
  addTopologyNode, createTopologyDraft, deleteTopologyEdge, removeTopologyNode, type TopologyDraftState,
} from "@/lib/correlation-topology-draft";
import type { CorrelationTopologyAnalysis } from "@/api/types";
import { TopologyEditor } from "./TopologyEditor";

const analysis: CorrelationTopologyAnalysis = {
  analysis_id: "topology-1", workspace: "ws1", status: "completed", repos: ["web", "order"],
  result: {
    nodes: [{ repo: "web", roles: ["entrypoint", "backend"], capabilities: [] },
      { repo: "order", roles: ["backend"], capabilities: [] }],
    edges: [{ from: "web", to: "order", protocol: "grpc", confidence: "medium",
      client_evidence: [{ repo: "web", file: "client.ts", line: 1, snippet: "stub" }],
      handler_evidence: [] }],
    uncertain: [], coverage: [], invalid: [],
  },
};

function state(): TopologyDraftState {
  return createTopologyDraft(["web", "order"], analysis, {});
}

it("supports SVG graph and accessible table editing parity", async () => {
  let current = state();
  const onState = (next: typeof current) => { current = next; };
  const { rerender } = render(<TopologyEditor state={current} onState={onState} availableRepos={["web", "order", "payment"]} onAddNode={(repo) => { current = addTopologyNode(current, repo); }} />);
  expect(screen.getByTestId("topology-node-web")).toBeInTheDocument();
  expect(screen.getByTestId("topology-edge-ai_web-_order_grpc")).toBeInTheDocument();

  fireEvent.click(screen.getByTestId("topology-edge-ai_web-_order_grpc"));
  expect(screen.getByText(/client\.ts:1/)).toBeInTheDocument();

  const role = screen.getByRole("checkbox", { name: /web.*backend/ });
  fireEvent.click(role);
  rerender(<TopologyEditor state={current} onState={onState} availableRepos={["web", "order", "payment"]} onAddNode={(repo) => { current = addTopologyNode(current, repo); }} />);
  expect(current.draft.nodes[0].roles).toEqual(["entrypoint"]);

  // 协议下拉：Radix Select 交互（click trigger → click option；原生 select 已换 ui/Select）
  fireEvent.click(screen.getByRole("combobox", { name: "protocol" }));
  fireEvent.click(await screen.findByRole("option", { name: "http" }));
  rerender(<TopologyEditor state={current} onState={onState} availableRepos={["web", "order", "payment"]} onAddNode={(repo) => { current = addTopologyNode(current, repo); }} />);
  expect(current.draft.edges[0].protocol).toBe("http");

  fireEvent.click(screen.getAllByRole("button", { name: /delete edge/i }).at(-1)!);
  rerender(<TopologyEditor state={current} onState={onState} availableRepos={["web", "order", "payment"]} onAddNode={(repo) => { current = addTopologyNode(current, repo); }} />);
  expect(current.draft.edges).toHaveLength(0);
  fireEvent.click(screen.getByRole("combobox", { name: /select service|选择服务/i }));
  fireEvent.click(await screen.findByRole("option", { name: "payment" }));
  fireEvent.click(screen.getByRole("button", { name: /add service|添加服务/i }));
  rerender(<TopologyEditor state={current} onState={onState} availableRepos={["web", "order", "payment"]} />);
  expect(current.draft.nodes.some((node) => node.repo === "payment")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: /undo/i }));
  rerender(<TopologyEditor state={current} onState={onState} availableRepos={["web", "order", "payment"]} />);
  fireEvent.click(screen.getByRole("button", { name: /undo/i }));
  rerender(<TopologyEditor state={current} onState={onState} availableRepos={["web", "order", "payment"]} onAddNode={(repo) => { current = addTopologyNode(current, repo); }} />);
  expect(current.draft.edges).toHaveLength(1);
});

it("retains and restores deleted AI evidence", () => {
  let current = state();
  const onState = (next: typeof current) => { current = next; };
  const { rerender } = render(<TopologyEditor state={current} onState={onState} />);
  const edgeId = current.draft.edges[0].id;
  current = deleteTopologyEdge(current, edgeId);
  rerender(<TopologyEditor state={current} onState={onState} />);
  expect(current.draft.edges).toHaveLength(0);
  expect(screen.getByText(/client\.ts:1/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /restore|恢复/i }));
  rerender(<TopologyEditor state={current} onState={onState} />);
  expect(current.draft.edges).toHaveLength(1);
  expect(current.draft.edges[0].origin).toBe("ai");
});


it("supports keyboard-accessible node deletion", () => {
  let current = state();
  const onState = (next: typeof current) => { current = next; };
  const { rerender } = render(
    <TopologyEditor state={current} onState={onState}
      onRemoveNode={(repo) => { current = removeTopologyNode(current, repo); }} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Delete web" }));
  rerender(<TopologyEditor state={current} onState={onState}
    onRemoveNode={(repo) => { current = removeTopologyNode(current, repo); }} />);
  expect(current.draft.nodes.some((node) => node.repo === "web")).toBe(false);
  expect(current.draft.edges).toHaveLength(0);
});

/** 画布主 svg（data-testid 锚定——container.querySelector("svg") 会选到 undo 按钮
 *  里的 lucide 图标 svg，事件派发/坐标 mock 全会错位）。 */
function canvasSvg(container: HTMLElement) {
  return container.querySelector('svg[data-testid="topology-canvas"]')!;
}

/** jsdom 的 SVG getBoundingClientRect 全 0——mock 成 viewBox 等比，坐标换算才可用。 */
function mockSvgRect(container: HTMLElement) {
  const svg = canvasSvg(container);
  vi.spyOn(svg, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, left: 0, top: 0, right: 800, bottom: 600, width: 800, height: 600,
    toJSON: () => "",
  } as DOMRect);
  return svg;
}

/** jsdom 的 PointerEvent 构造器丢鼠标坐标（clientX/Y 恒 undefined，NaN 污染坐标换算）
 *  ——MouseEvent 载体自定义 type 派发：React 合成事件按 type 分发，坐标可用；
 *  fireEvent(el, event) 同时保证 act 包裹（原生 dispatchEvent 不在 act 内，
 *  listener 里的 setState 异步调度，同步断言读不到）。 */
function firePointer(el: Element, type: "pointerdown" | "pointermove", x: number, y: number) {
  fireEvent(el, new MouseEvent(type, { clientX: x, clientY: y, bubbles: true, cancelable: true }));
}

it("拖线式连接：按住手柄出现预览，拖到目标节点上松手建 manual 边；Esc 取消", () => {
  let current = state();
  const onState = (next: typeof current) => { current = next; };
  const { rerender, container } = render(<TopologyEditor state={current} onState={onState} />);
  const svg = mockSvgRect(container);
  const handle = screen.getByRole("button", { name: /Connect web/i });
  fireEvent.pointerDown(handle);
  expect(screen.getByTestId("topology-connect-preview")).toBeInTheDocument();
  firePointer(svg, "pointermove", 400, 120);
  // Esc 取消：预览消失，不建边
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByTestId("topology-connect-preview")).toBeNull();
  // 再拖一次，在目标节点上松手 → 建 manual 边
  fireEvent.pointerDown(handle);
  firePointer(svg, "pointermove", 400, 120);
  fireEvent.pointerUp(screen.getByTestId("topology-node-order"));
  rerender(<TopologyEditor state={current} onState={onState} />);
  expect(current.draft.edges.some((e) => e.from === "web" && e.to === "order" && e.origin === "manual")).toBe(true);
});

it("空白处松手取消连线（不建边）", () => {
  let current = state();
  const onState = (next: typeof current) => { current = next; };
  const { rerender, container } = render(<TopologyEditor state={current} onState={onState} />);
  const svg = mockSvgRect(container);
  fireEvent.pointerDown(screen.getByRole("button", { name: /Connect web/i }));
  firePointer(svg, "pointermove", 500, 300);
  fireEvent.pointerUp(svg);
  rerender(<TopologyEditor state={current} onState={onState} />);
  expect(current.draft.edges.every((e) => e.origin !== "manual")).toBe(true);
});

it("节点拖动 clamp 在画布内（拖出边界不再丢失节点）", () => {
  let current = state();
  const onState = (next: typeof current) => { current = next; };
  const { rerender, container } = render(<TopologyEditor state={current} onState={onState} />);
  const svg = mockSvgRect(container);
  fireEvent.pointerDown(screen.getByTestId("topology-node-web"));
  firePointer(svg, "pointermove", 1200, -300);
  rerender(<TopologyEditor state={current} onState={onState} />);
  const web = current.draft.nodes.find((n) => n.repo === "web")!;
  // 指针 (1200,-300) 越界 → clamp 至 [6, 800-105-6]×[6, 600-48-6] 的右上角界内
  expect(web.position.x).toBe(800 - 105 - 6);
  expect(web.position.y).toBe(6);
});

/* ===== 画布 pan/zoom ===== */

/** 解析视口组 transform（"translate(x y) scale(k)"）为数值。 */
function parseViewport(container: HTMLElement) {
  const transform = container.querySelector('g[data-testid="topology-viewport"]')!.getAttribute("transform")!;
  const m = transform.match(/translate\((-?[\d.]+) (-?[\d.]+)\) scale\(([\d.]+)\)/)!;
  return { x: Number(m[1]), y: Number(m[2]), k: Number(m[3]) };
}

it("滚轮缩放：指针锚定（指针下世界点不动），preventDefault 拦页面滚动", () => {
  const { container } = render(<TopologyEditor state={state()} onState={() => {}} />);
  const svg = mockSvgRect(container);
  const wheel = new WheelEvent("wheel", { deltaY: -240, clientX: 400, clientY: 300, cancelable: true });
  // fireEvent 包装（act 同步）——原生 dispatchEvent 下 listener 的 setView 异步调度
  fireEvent(svg, wheel);
  expect(wheel.defaultPrevented).toBe(true);
  const v = parseViewport(container);
  const factor = Math.exp(240 * 0.0016);
  expect(v.k).toBeCloseTo(factor, 3);
  // 锚定断言：视口 (400,300) 处的世界点缩放前后都是 (400,300)
  expect(v.x).toBeCloseTo(400 - 400 * factor, 1);
  expect(v.y).toBeCloseTo(300 - 300 * factor, 1);
});

it("空白处拖动平移画布（节点落点不触发），拖动态切 grabbing 光标", () => {
  const { container } = render(<TopologyEditor state={state()} onState={() => {}} />);
  const svg = mockSvgRect(container);
  // 节点上落点：不开平移（仍走节点拖动），pointerUp 收尾清 dragRepo
  fireEvent.pointerDown(screen.getByTestId("topology-node-web"));
  firePointer(svg, "pointermove", 500, 320);
  expect(parseViewport(container)).toEqual({ x: 0, y: 0, k: 1 });
  fireEvent.pointerUp(svg);
  // 空白落点：平移 + grabbing 光标
  firePointer(svg, "pointerdown", 200, 100);
  expect(svg.getAttribute("class")).toContain("cursor-grabbing");
  firePointer(svg, "pointermove", 500, 320);
  expect(parseViewport(container)).toEqual({ x: 300, y: 220, k: 1 });
  fireEvent.pointerUp(svg);
  expect(svg.getAttribute("class")).toContain("cursor-grab");
  expect(parseViewport(container)).toEqual({ x: 300, y: 220, k: 1 });
});

it("缩放条：＋/− 中心步进、百分比回 100%、fit 适配全图且不放大", () => {
  const { container } = render(<TopologyEditor state={state()} onState={() => {}} />);
  mockSvgRect(container);
  // 默认两节点 (80,70)-(585,228) 全在视口内：fit 按钮无溢出提示点
  expect(screen.queryByTestId("topology-fit-dot")).toBeNull();
  const zoomIn = screen.getByRole("button", { name: /zoom in|放大/i });
  fireEvent.click(zoomIn);
  const stepped = parseViewport(container);
  expect(stepped.k).toBeCloseTo(1.2, 10);
  expect(stepped.x).toBeCloseTo(-80, 6);
  expect(stepped.y).toBeCloseTo(-60, 6);
  // 放大后节点溢出视口 → fit 亮提示点
  fireEvent.click(zoomIn);
  expect(screen.getByTestId("topology-fit-dot")).toBeInTheDocument();
  const zoomOut = screen.getByRole("button", { name: /zoom out|缩小/i });
  fireEvent.click(zoomOut);
  expect(parseViewport(container).k).toBeCloseTo(1.2, 5);
  // 百分比读数 + 点击回 100%
  expect(screen.getByRole("button", { name: /reset zoom|重置缩放/i }).textContent).toBe("120%");
  fireEvent.click(screen.getByRole("button", { name: /reset zoom|重置缩放/i }));
  expect(parseViewport(container)).toEqual({ x: 0, y: 0, k: 1 });
  // fit：bbox (80,70)-(585,228) → 只缩小不放大，k=1 居中
  fireEvent.click(screen.getByRole("button", { name: /fit all services|全图适配/i }));
  const fit = parseViewport(container);
  expect(fit.k).toBe(1);
  expect(fit.x).toBeCloseTo((800 - 505) / 2 - 80, 5);
  expect(fit.y).toBeCloseTo((600 - 158) / 2 - 70, 5);
});

it("缩放态下拖节点：指针坐标逆变换到世界坐标（不随缩放跑偏）", () => {
  let current = state();
  const onState = (next: typeof current) => { current = next; };
  const { rerender, container } = render(<TopologyEditor state={current} onState={onState} />);
  const svg = mockSvgRect(container);
  // 放大 1.2×（中心锚定 → translate(-80,-60)）
  fireEvent.click(screen.getByRole("button", { name: /zoom in|放大/i }));
  const zoomed = parseViewport(container);
  expect(zoomed.k).toBeCloseTo(1.2, 10);
  expect(zoomed.x).toBeCloseTo(-80, 6);
  expect(zoomed.y).toBeCloseTo(-60, 6);
  fireEvent.pointerDown(screen.getByTestId("topology-node-web"));
  firePointer(svg, "pointermove", 400, 300);
  rerender(<TopologyEditor state={current} onState={onState} />);
  const web = current.draft.nodes.find((n) => n.repo === "web")!;
  // 世界坐标 = ((400+80)/1.2, (300+60)/1.2) = (400, 300)
  expect(web.position.x).toBeCloseTo(400, 5);
  expect(web.position.y).toBeCloseTo(300, 5);
});
