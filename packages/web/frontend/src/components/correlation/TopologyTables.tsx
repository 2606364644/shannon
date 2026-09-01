import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import type { CorrRole } from "@/lib/correlation-yaml";
import {
  addTopologyEdge, deleteTopologyEdge, setTopologyNodeSource, setTopologyReferenceOnly,
  toggleTopologyRole, updateTopologyEdge, type TopologyDraft, type TopologyDraftState,
} from "@/lib/correlation-topology-draft";

interface Props {
  draft: TopologyDraft;
  scans: unknown[];
  onState: (state: TopologyDraftState) => void;
  state: TopologyDraftState;
  selectedEdgeId?: string | null;
  onSelectEdge: (id: string) => void;
  availableRepos?: string[];
  onAddNode?: (repo: string) => void;
  onRemoveNode?: (repo: string) => void;
}

export function TopologyTables({
  draft, scans, onState, state, onSelectEdge, availableRepos = [],
  onAddNode, onRemoveNode,
}: Props) {
  const { t } = useTranslation();
  const candidates = availableRepos.filter((repo) => !draft.nodes.some((node) => node.repo === repo));
  const [newNode, setNewNode] = useState("");
  const repos = draft.nodes;
  const scanList = scans as Array<{ scan_id?: string; id?: string; repo?: string; scan_type?: string; status?: string }>;
  const whiteboxScans = scanList.filter((scan) => scan.scan_type === "whitebox" && scan.status === "completed");
  return (
    <div className="grid gap-3">
      <section className="overflow-x-auto rounded-lg border border-border" aria-label={t("scan.correlation.topology.nodeTable")}>
        <table className="w-full text-xs">
          <thead className="bg-muted/50 text-muted-foreground">
            <tr><th className="p-2 text-left">{t("scan.correlation.colService")}</th>
              <th className="p-2 text-left">{t("scan.correlation.roleLabel")}</th>
              <th className="p-2 text-left">{t("scan.correlation.sourceLabel")}</th>
              <th className="p-2">{t("scan.correlation.topology.referenceOnly")}</th><th className="p-2" /></tr>
          </thead>
          <tbody>
            {repos.map((node) => (
              <tr key={node.repo} className="border-t border-border">
                <td className="p-2 font-mono">{node.repo}</td>
                <td className="flex gap-3 p-2">
                  {(["entrypoint", "backend"] as CorrRole[]).map((role) => (
                    <label key={role} className="flex items-center gap-1">
                      <Checkbox checked={node.roles.includes(role)}
                        onCheckedChange={() => onState(toggleTopologyRole(state, node.repo, role))}
                        aria-label={`${node.repo} ${role}`} />
                      {t(`scan.correlation.role${role === "entrypoint" ? "Entrypoint" : "Backend"}`)}
                    </label>
                  ))}
                </td>
                <td className="p-2">
                  <select className="h-7 rounded border border-input bg-background px-1" aria-label={`${node.repo} source`}
                    value={node.reuseScanId ?? ""} onChange={(e) => onState(setTopologyNodeSource(state, node.repo, e.target.value || null))}>
                    <option value="">{t("scan.correlation.sourceRescan")}</option>
                    {whiteboxScans.filter((scan) => scan.repo === node.repo && (scan as { status?: string }).status !== "running").map((scan, i) => (
                      <option key={scan.scan_id ?? scan.id ?? i} value={scan.scan_id ?? scan.id ?? ""}>
                        {t("scan.correlation.sourceReuse")}: {scan.scan_id ?? scan.id}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="p-2 text-center">
                  <Checkbox checked={node.referenceOnly === true} aria-label={`${node.repo} reference only`}
                    onCheckedChange={(v) => onState(setTopologyReferenceOnly(state, node.repo, v === true))} />
                </td>
                <td className="p-2 text-right">
                  <Button type="button" variant="ghost" size="sm"
                    aria-label={`${t("common.delete")} ${node.repo}`} disabled={!onRemoveNode}
                    onClick={() => onRemoveNode?.(node.repo)}>{t("common.delete")}</Button>
                </td>
              </tr>
            ))}
            <tr className="border-t border-border">
              <td colSpan={5} className="flex flex-wrap items-center gap-2 p-2">
                <select className="h-7 rounded border border-input bg-background" aria-label={t("scan.correlation.topology.selectNode")}
                  value={newNode} onChange={(event) => setNewNode(event.target.value)}>
                  <option value="">{t("scan.correlation.topology.selectNode")}</option>
                  {candidates.map((repo) => <option key={repo}>{repo}</option>)}
                </select>
                <Button type="button" variant="outline" size="sm"
                  disabled={!onAddNode || !newNode}
                  onClick={() => { onAddNode?.(newNode); setNewNode(""); }}>
                  {t("scan.correlation.topology.addNode")}
                </Button>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
      <section className="overflow-x-auto rounded-lg border border-border" aria-label={t("scan.correlation.topology.edgeTable")}>
        <table className="w-full text-xs">
          <thead className="bg-muted/50 text-muted-foreground">
            <tr><th className="p-2 text-left">{t("scan.correlation.colFrom")}</th><th className="p-2 text-left">{t("scan.correlation.colTo")}</th>
              <th className="p-2 text-left">{t("scan.correlation.protocolLabel")}</th>
              <th className="p-2">{t("scan.correlation.topology.enabled")}</th><th className="p-2" /></tr>
          </thead>
          <tbody>
            {draft.edges.map((edge) => (
              <tr key={edge.id} className="border-t border-border">
                <td className="p-2"><select className="h-7 rounded border border-input bg-background" aria-label={`${edge.id} from`}
                  value={edge.from} onChange={(e) => onState(updateTopologyEdge(state, edge.id, { from: e.target.value }))}>
                  {repos.map((node) => <option key={node.repo}>{node.repo}</option>)}
                </select></td>
                <td className="p-2"><select className="h-7 rounded border border-input bg-background" aria-label={`${edge.id} to`}
                  value={edge.to} onChange={(e) => onState(updateTopologyEdge(state, edge.id, { to: e.target.value }))}>
                  {repos.map((node) => <option key={node.repo}>{node.repo}</option>)}
                </select></td>
                <td className="p-2"><select className="h-7 rounded border border-input bg-background" aria-label="protocol"
                  value={edge.protocol} onChange={(e) => onState(updateTopologyEdge(state, edge.id, { protocol: e.target.value as never }))}>
                  {["grpc", "http", "graphql"].map((p) => <option key={p}>{p}</option>)}
                </select></td>
                <td className="p-2 text-center"><Checkbox checked={edge.enabled} aria-label={`${edge.id} enabled`}
                  onCheckedChange={(v) => onState(updateTopologyEdge(state, edge.id, { enabled: v === true }))} /></td>
                <td className="flex justify-end gap-1 p-2">
                  <Button type="button" variant="ghost" size="sm" onClick={() => onSelectEdge(edge.id)}>{t("scan.correlation.evidence")}</Button>
                  <Button type="button" variant="ghost" size="sm" aria-label={t("scan.correlation.topology.deleteEdge")}
                    onClick={() => onState(deleteTopologyEdge(state, edge.id))}>{t("common.delete")}</Button>
                </td>
              </tr>
            ))}
            <tr className="border-t border-border">
              <td colSpan={5} className="p-2">
                <Button type="button" variant="outline" size="sm" disabled={repos.length < 2}
                  onClick={() => onState(addTopologyEdge(state, { from: repos[0].repo, to: repos[1].repo, protocol: "grpc" }))}>
                  {t("scan.correlation.topology.addEdge")}
                </Button>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  );
}
