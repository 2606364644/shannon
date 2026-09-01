import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Redo2, Undo2, LayoutGrid } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  addTopologyEdge, deleteTopologyEdge, moveTopologyNode, redoTopology, undoTopology,
  resetTopologyLayout, restoreTopologyAiEdge, setTopologyEdgeEnabled, updateTopologyEdge,
  validateTopologyDraft, type TopologyDraftState,
} from "@/lib/correlation-topology-draft";
import { TopologyTables } from "./TopologyTables";

interface Props {
  state: TopologyDraftState;
  onState: (state: TopologyDraftState) => void;
  scans?: unknown[];
  availableRepos?: string[];
  onAddNode?: (repo: string) => void;
  onRemoveNode?: (repo: string) => void;
}

export function TopologyEditor({ state, onState, scans = [], availableRepos, onAddNode, onRemoveNode }: Props) {
  const { t } = useTranslation();
  const svgRef = useRef<SVGSVGElement>(null);
  const dragRepo = useRef<string | null>(null);
  const connectFrom = useRef<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const selectedEdge = state.draft.edges.find((edge) => edge.id === selectedEdgeId) ?? null;
  const liveIdentities = new Set(state.draft.edges.map((edge) => `${edge.from}\n${edge.to}\n${edge.protocol}`));
  const removedAiEdges = (state.analysis?.result?.edges ?? []).filter(
    (edge) => !liveIdentities.has(`${edge.from}\n${edge.to}\n${edge.protocol}`));
  const nodeByRepo = new Map(state.draft.nodes.map((node) => [node.repo, node]));

  const point = (event: React.PointerEvent) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return { x: (event.clientX - rect.left) * (800 / rect.width), y: (event.clientY - rect.top) * (600 / rect.height) };
  };

  return (
    <section className="space-y-3" aria-label={t("scan.correlation.topology.editor")}>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="outline" size="sm" disabled={!state.history.past.length}
          onClick={() => onState(undoTopology(state))}><Undo2 className="h-3.5 w-3.5" />{t("scan.correlation.topology.undo")}</Button>
        <Button type="button" variant="outline" size="sm" disabled={!state.history.future.length}
          onClick={() => onState(redoTopology(state))}><Redo2 className="h-3.5 w-3.5" />{t("scan.correlation.topology.redo")}</Button>
        <Button type="button" variant="outline" size="sm" onClick={() => onState(resetTopologyLayout(state))}>
          <LayoutGrid className="h-3.5 w-3.5" />{t("scan.correlation.topology.resetLayout")}
        </Button>
      </div>
      <div className="grid gap-3 xl:grid-cols-[minmax(0,2fr)_minmax(18rem,1fr)]">
        <svg ref={svgRef} viewBox="0 0 800 600" className="h-[420px] w-full rounded-lg border border-border bg-card"
          onPointerMove={(event) => {
            if (!dragRepo.current) return;
            const p = point(event);
            onState(moveTopologyNode(state, dragRepo.current, p.x, p.y));
          }}
          onPointerUp={() => { dragRepo.current = null; connectFrom.current = null; }}>
          <defs><marker id="topology-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" className="fill-muted-foreground" />
          </marker></defs>
          {state.draft.edges.map((edge) => {
            const from = nodeByRepo.get(edge.from); const to = nodeByRepo.get(edge.to);
            if (!from || !to) return null;
            return <line key={edge.id} data-testid={`topology-edge-${edge.id.replace(/[^A-Za-z0-9_-]/g, "_")}`}
              x1={from.position.x + 105} y1={from.position.y + 24} x2={to.position.x} y2={to.position.y + 24}
              className={edge.enabled ? (selectedEdgeId === edge.id ? "stroke-primary" : "stroke-muted-foreground") : "stroke-border"}
              strokeWidth={selectedEdgeId === edge.id ? 2.5 : 1.5} strokeDasharray={edge.enabled ? undefined : "4 4"}
              markerEnd="url(#topology-arrow)" tabIndex={0} role="button" aria-label={`${edge.from} ${edge.protocol} ${edge.to}`}
              onPointerDown={() => setSelectedEdgeId(edge.id)} onClick={() => setSelectedEdgeId(edge.id)}
              onKeyDown={(e) => e.key === "Enter" && setSelectedEdgeId(edge.id)} />;
          })}
          {state.draft.nodes.map((node) => (
            <g key={node.repo} data-testid={`topology-node-${node.repo}`} data-node={node.repo} tabIndex={0} role="group"
              aria-label={`${node.repo} ${node.roles.join(", ")}`}
              onPointerDown={() => {
                if (connectFrom.current && connectFrom.current !== node.repo)
                  onState(addTopologyEdge(state, { from: connectFrom.current, to: node.repo, protocol: "grpc" }));
                connectFrom.current = null; dragRepo.current = node.repo;
              }}
              onPointerUp={() => { dragRepo.current = null; }}>
              <rect x={node.position.x} y={node.position.y} width={105} height={48} rx={8}
                className="fill-secondary stroke-border" />
              <text x={node.position.x + 10} y={node.position.y + 20} className="fill-foreground font-mono text-[11px]">{node.repo}</text>
              <text x={node.position.x + 10} y={node.position.y + 37} className="fill-muted-foreground text-[9px]">{node.roles.join(" · ") || "—"}</text>
              <circle cx={node.position.x + 112} cy={node.position.y + 24} r={6} className="fill-primary/70"
                aria-label={`${t("scan.correlation.topology.connect")} ${node.repo}`} role="button" tabIndex={0}
                onPointerDown={(e) => { e.stopPropagation(); connectFrom.current = node.repo; }} />
            </g>
          ))}
        </svg>
        <aside className="space-y-3 rounded-lg border border-border bg-card p-3" aria-label={t("scan.correlation.topology.details")}>
          {selectedEdge ? (
            <div className="space-y-2 text-xs">
              <div className="font-semibold">{selectedEdge.from} → {selectedEdge.to}</div>
              <div className="text-muted-foreground">
                {selectedEdge.confidence ?? "unknown"}
                {selectedEdge.service ? ` · ${selectedEdge.service}` : ""}
                {selectedEdge.method ? ` · ${selectedEdge.method}` : ""}
              </div>
              <label className="block space-y-1">
                <span className="text-muted-foreground">{t("scan.correlation.protocolLabel")}</span>
                <select className="h-8 w-full rounded border border-input bg-background" value={selectedEdge.protocol}
                  onChange={(e) => onState(updateTopologyEdge(state, selectedEdge.id, { protocol: e.target.value as never }))}>
                  {["grpc", "http", "graphql"].map((p) => <option key={p}>{p}</option>)}
                </select>
              </label>
              <label className="flex items-center gap-2">
                <Checkbox checked={selectedEdge.enabled}
                  onCheckedChange={(v) => onState(setTopologyEdgeEnabled(state, selectedEdge.id, v === true))} />
                {t("scan.correlation.topology.enabled")}
              </label>
              <Button type="button" variant="outline" size="sm" onClick={() => onState(deleteTopologyEdge(state, selectedEdge.id))}>
                {t("scan.correlation.topology.deleteEdge")}
              </Button>
              <div className="space-y-1 font-mono text-[10px] text-muted-foreground">
                {[...(selectedEdge.client_evidence ?? []), ...(selectedEdge.handler_evidence ?? [])].map((ev, i) => (
                  <div key={`${ev.repo}-${ev.file}-${i}`}>
                    {ev.repo}/{ev.file}:{ev.line ?? "?"} — {ev.snippet}
                    {ev.valid === false ? ` · invalid: ${ev.validation_errors?.join(", ")}` : ""}
                  </div>
                ))}
              </div>
            </div>
          ) : <p className="text-xs text-muted-foreground">{t("scan.correlation.topology.selectEdge")}</p>}
          <div className="space-y-1 border-t border-border pt-2 text-[11px] text-muted-foreground">
            {state.draft.nodes.flatMap((node) => (node.capabilities ?? []).flatMap((capability) =>
              capability.evidence.map((ev, index) => (
                <div key={`${node.repo}-${capability.role}-${ev.file}-${index}`}>
                  {capability.role} ({capability.confidence}) {node.repo}/{ev.file}:{ev.line ?? "?"} — {ev.snippet}
                  {ev.valid === false ? ` · invalid: ${ev.validation_errors?.join(", ")}` : ""}
                </div>
              ))
            ))}
            {removedAiEdges.length > 0 && (
              <div className="space-y-2 border-t border-border pt-2">
                <div className="text-xs font-semibold">{t("scan.correlation.topology.removedAi")}</div>
                {removedAiEdges.map((edge) => (
                  <div key={`${edge.from}-${edge.to}-${edge.protocol}`} className="space-y-1">
                    <div className="font-mono">{edge.from} → {edge.to} ({edge.protocol})</div>
                    <div className="text-muted-foreground">
                      {edge.confidence ?? "unknown"}
                      {edge.service ? ` · ${edge.service}` : ""}
                      {edge.method ? ` · ${edge.method}` : ""}
                    </div>
                    {[...(edge.client_evidence ?? []), ...(edge.handler_evidence ?? [])].map((ev, i) => (
                      <div key={`${edge.from}-${edge.to}-${ev.file}-${i}`}>
                        {ev.repo}/{ev.file}:{ev.line ?? "?"} — {ev.snippet}
                        {ev.valid === false ? ` · invalid: ${ev.validation_errors?.join(", ")}` : ""}
                      </div>
                    ))}
                    <Button type="button" variant="outline" size="sm"
                      onClick={() => onState(restoreTopologyAiEdge(state, edge))}>
                      {t("scan.correlation.topology.restore")}
                    </Button>
                  </div>
                ))}
              </div>
            )}
            {state.draft.coverage.map((c) => <div key={c.repo}>{c.repo}: {c.complete ? "✓" : "○"} {c.reason}</div>)}
            {state.draft.uncertain.map((u, i) => <div key={`${u.repo}-${i}`}>? {u.repo}: {u.message}{u.protocol_hint ? ` (${u.protocol_hint})` : ""}</div>)}
          </div>
        </aside>
      </div>
      <TopologyTables draft={state.draft} state={state} onState={onState} scans={scans}
        selectedEdgeId={selectedEdgeId} onSelectEdge={setSelectedEdgeId}
        availableRepos={availableRepos} onAddNode={onAddNode} onRemoveNode={onRemoveNode} />
      {validateTopologyDraft(state.draft).map((issue) => (
        <p key={issue.code + issue.message} role="alert" className="text-xs text-destructive">
          {t(`scan.correlation.issues.${issue.code}`, { defaultValue: issue.message })}
        </p>
      ))}
    </section>
  );
}
