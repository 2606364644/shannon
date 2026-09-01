import { describe, expect, it } from "vitest";
import yaml from "js-yaml";
import {
  addTopologyEdge,
  confirmTopologyDraft,
  createTopologyDraft,
  deleteTopologyEdge,
  moveTopologyNode,
  resetTopologyLayout,
  setTopologyEdgeEnabled,
  setTopologyNodeSource,
  addTopologyNode,
  removeTopologyNode,
  updateTopologyRepositories,
  toggleTopologyRole,
  topologyDraftFingerprint,
  topologyDraftToCorrForm,
  undoTopology,
  redoTopology,
  validateTopologyDraft,
} from "./correlation-topology-draft";
import type { CorrelationTopologyAnalysis } from "@/api/types";

const analysis: CorrelationTopologyAnalysis = {
  analysis_id: "topology-test",
  workspace: "ws1",
  status: "completed",
  repos: ["web", "admin", "order", "user"],
  cache_hit: false,
  progress: 100,
  result: {
    nodes: [
      { repo: "web", roles: ["entrypoint", "backend"], capabilities: [] },
      { repo: "admin", roles: ["entrypoint"], capabilities: [] },
      { repo: "order", roles: ["backend"], capabilities: [] },
      { repo: "user", roles: ["backend"], capabilities: [] },
    ],
    edges: [
      {
        from: "web", to: "order", protocol: "grpc", confidence: "high",
        client_evidence: [{ repo: "web", file: "client.ts", line: 1, snippet: "stub" }],
        handler_evidence: [],
      },
      { from: "web", to: "user", protocol: "http", confidence: "medium", client_evidence: [], handler_evidence: [] },
      { from: "admin", to: "order", protocol: "graphql", confidence: "medium", client_evidence: [], handler_evidence: [] },
      { from: "order", to: "user", protocol: "grpc", confidence: "low", client_evidence: [], handler_evidence: [] },
    ],
    uncertain: [{ repo: "order", message: "possible thrift", protocol_hint: "thrift", evidence: [] }],
    coverage: analysisRepos().map((repo) => ({ repo, complete: true, reason: "fixture" })),
    invalid: [],
  },
};

function analysisRepos() { return ["web", "admin", "order", "user"]; }

describe("topology draft graph semantics", () => {
  it("imports all selected repos and retains M:N plus multi-hop edges", () => {
    const state = createTopologyDraft(analysis.repos, analysis, { web: "scan-web" });
    expect(state.draft.nodes.map((n) => n.repo)).toEqual(analysis.repos);
    expect(state.draft.edges.map((e) => `${e.from}-${e.to}-${e.protocol}`)).toEqual([
      "web-order-grpc", "web-user-http", "admin-order-graphql", "order-user-grpc",
    ]);
    expect(state.draft.nodes[0].roles).toEqual(["entrypoint", "backend"]);
    expect(state.draft.nodes[0].reuseScanId).toBe("scan-web");
    expect(state.draft.nodes[1].reuseScanId).toBeNull();
    expect(state.draft.uncertain).toHaveLength(1);
  });

  it("supports edits, undo/redo, source preservation, and confirmation invalidation", () => {
    let state = createTopologyDraft(analysis.repos, analysis, {});
    state = confirmTopologyDraft(state);
    expect(state.confirmation.status).toBe("confirmed");
    const confirmedFingerprint = state.confirmation.fingerprint;

    state = setTopologyEdgeEnabled(state, state.draft.edges[1].id, false);
    expect(state.confirmation.status).toBe("unconfirmed");
    expect(state.draft.edges[1].enabled).toBe(false);

    state = setTopologyNodeSource(state, "web", "scan-web");
    expect(state.draft.nodes[0].reuseScanId).toBe("scan-web");

    state = addTopologyEdge(state, { from: "admin", to: "user", protocol: "grpc" });
    expect(state.draft.edges.at(-1)?.origin).toBe("manual");

    const deletedId = state.draft.edges[0].id;
    state = deleteTopologyEdge(state, deletedId);
    expect(state.draft.edges.some((e) => e.id === deletedId)).toBe(false);
    state = undoTopology(state); state = undoTopology(state); state = undoTopology(state);
    expect(state.draft.edges[0].enabled).toBe(true);
    expect(state.draft.nodes[0].reuseScanId).toBeNull();
    state = redoTopology(state);
    expect(state.draft.nodes[0].reuseScanId).toBe("scan-web");

    state = confirmTopologyDraft(state);
    expect(state.confirmation.fingerprint).not.toBeNull();
    expect(topologyDraftFingerprint(state.draft)).toBe(state.confirmation.fingerprint);
    expect(confirmedFingerprint).not.toBeNull();
  });

  it("layout-only operations do not invalidate confirmation or enter undo history", () => {
    let state = confirmTopologyDraft(createTopologyDraft(analysis.repos, analysis, {}));
    const before = state.draft.nodes[0].position;
    state = moveTopologyNode(state, "web", 123, 456);
    expect(state.draft.nodes[0].position).toEqual({ x: 123, y: 456 });
    expect(state.confirmation.status).toBe("confirmed");
    state = resetTopologyLayout(state);
    expect(state.draft.nodes[0].position).toEqual(before);
    expect(undoTopology(state)).toBe(state);
  });

  it("preserves compatible draft state when repositories are added or removed", () => {
    let state = createTopologyDraft(analysis.repos, analysis, { web: "scan-web" });
    state = addTopologyNode(state, "payment");
    expect(state.draft.nodes.map((node) => node.repo)).toContain("payment");
    state = updateTopologyRepositories(state, ["web", "admin", "order", "payment"]);
    expect(state.draft.nodes.find((node) => node.repo === "user")).toBeUndefined();
    expect(state.draft.edges.every((edge) => edge.to !== "user" && edge.from !== "user")).toBe(true);
    expect(state.draft.nodes.find((node) => node.repo === "web")?.reuseScanId).toBe("scan-web");
    state = removeTopologyNode(state, "payment");
    expect(state.draft.nodes.map((node) => node.repo)).not.toContain("payment");
    expect(state.history.past.length).toBeGreaterThan(0);
  });

  it("converts only enabled edges, emits roles, and validates isolated/reference policy", () => {
    let state = createTopologyDraft(analysis.repos, analysis, {});
    state = setTopologyEdgeEnabled(state, state.draft.edges[1].id, false);
    const form = topologyDraftToCorrForm(state.draft);
    expect(form.relations).toHaveLength(3);
    expect(form.repos.find((r) => r.repo === "web")?.roles).toEqual(["entrypoint", "backend"]);
    expect(form.repos.find((r) => r.repo === "user")?.protocol).toBe("grpc");
    const parsed = yaml.load(confirmTopologyDraft(state).confirmation.yaml!) as any;
    expect(parsed.relations).toHaveLength(3);
    expect(parsed.repos.web.roles).toContain("backend");

    state = toggleTopologyRole(state, "web", "entrypoint");
    state = toggleTopologyRole(state, "admin", "entrypoint");
    expect(validateTopologyDraft(state.draft).map((i) => i.code)).toContain("missing_entrypoint");
    state = toggleTopologyRole(state, "web", "entrypoint");
    state = toggleTopologyRole(state, "admin", "entrypoint");
    state = setTopologyEdgeEnabled(state, state.draft.edges[3].id, false);
    const issues = validateTopologyDraft(state.draft);
    expect(issues.map((i) => i.code)).toContain("isolated_node");
    state.draft.nodes.find((n) => n.repo === "user")!.referenceOnly = true;
    expect(validateTopologyDraft(state.draft).filter((i) => i.code === "isolated_node")).toEqual([]);
  });
});
