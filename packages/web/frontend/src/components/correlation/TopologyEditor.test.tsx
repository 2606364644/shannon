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

/** jsdom 的 SVG getBoundingClientRect 全 0——mock 成 viewBox 等比，坐标换算才可用。 */
function mockSvgRect(container: HTMLElement) {
  const svg = container.querySelector("svg")!;
  vi.spyOn(svg, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, left: 0, top: 0, right: 800, bottom: 600, width: 800, height: 600,
    toJSON: () => "",
  } as DOMRect);
  return svg;
}

it("拖线式连接：按住手柄出现预览，拖到目标节点上松手建 manual 边；Esc 取消", () => {
  let current = state();
  const onState = (next: typeof current) => { current = next; };
  const { rerender, container } = render(<TopologyEditor state={current} onState={onState} />);
  const svg = mockSvgRect(container);
  const handle = screen.getByRole("button", { name: /Connect web/i });
  fireEvent.pointerDown(handle);
  expect(screen.getByTestId("topology-connect-preview")).toBeInTheDocument();
  fireEvent.pointerMove(svg, { clientX: 400, clientY: 120 });
  // Esc 取消：预览消失，不建边
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByTestId("topology-connect-preview")).toBeNull();
  // 再拖一次，在目标节点上松手 → 建 manual 边
  fireEvent.pointerDown(handle);
  fireEvent.pointerMove(svg, { clientX: 400, clientY: 120 });
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
  fireEvent.pointerMove(svg, { clientX: 500, clientY: 300 });
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
  fireEvent.pointerMove(svg, { clientX: 1200, clientY: -300 });
  rerender(<TopologyEditor state={current} onState={onState} />);
  const web = current.draft.nodes.find((n) => n.repo === "web")!;
  expect(web.position.x).toBeLessThanOrEqual(800 - 105 - 6);
  expect(web.position.y).toBeGreaterThanOrEqual(6);
});
