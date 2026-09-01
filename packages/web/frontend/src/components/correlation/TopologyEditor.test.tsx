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

it("supports SVG graph and accessible table editing parity", () => {
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

  const protocol = screen.getByRole("combobox", { name: "protocol" });
  fireEvent.change(protocol, { target: { value: "http" } });
  rerender(<TopologyEditor state={current} onState={onState} availableRepos={["web", "order", "payment"]} onAddNode={(repo) => { current = addTopologyNode(current, repo); }} />);
  expect(current.draft.edges[0].protocol).toBe("http");

  fireEvent.click(screen.getAllByRole("button", { name: /delete edge/i }).at(-1)!);
  rerender(<TopologyEditor state={current} onState={onState} availableRepos={["web", "order", "payment"]} onAddNode={(repo) => { current = addTopologyNode(current, repo); }} />);
  expect(current.draft.edges).toHaveLength(0);
  fireEvent.change(screen.getByRole("combobox", { name: /select service|选择服务/i }), { target: { value: "payment" } });
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
