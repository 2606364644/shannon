import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
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

/** 「重新扫描」哨兵值：radix SelectItem 不接受空串 value，重扫选项经哨兵映射回 null。 */
const RESCAN = "__rescan__";

/** 表内紧凑下拉（ui/Select 与全站同款：主题化浮层/键盘导航；表格行内用小号）。 */
function CellSelect({ ariaLabel, value, placeholder, onChange, options }: {
  ariaLabel: string;
  value: string | undefined;
  placeholder?: string;
  onChange: (v: string) => void;
  options: { value: string; label: React.ReactNode }[];
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger aria-label={ariaLabel} className="h-8 w-full text-xs">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {options.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
      </SelectContent>
    </Select>
  );
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
      <section className="overflow-hidden rounded-lg border border-border" aria-label={t("scan.correlation.topology.nodeTable")}>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("scan.correlation.colService")}</TableHead>
              <TableHead>{t("scan.correlation.roleLabel")}</TableHead>
              <TableHead>{t("scan.correlation.sourceLabel")}</TableHead>
              <TableHead className="text-center">{t("scan.correlation.topology.referenceOnly")}</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {repos.map((node) => (
              <TableRow key={node.repo}>
                <TableCell className="py-1.5 font-mono text-xs">{node.repo}</TableCell>
                <TableCell className="py-1.5">
                  <div className="flex gap-3">
                    {(["entrypoint", "backend"] as CorrRole[]).map((role) => (
                      <label key={role} className="flex items-center gap-1">
                        <Checkbox checked={node.roles.includes(role)}
                          onCheckedChange={() => onState(toggleTopologyRole(state, node.repo, role))}
                          aria-label={`${node.repo} ${role}`} />
                        {t(`scan.correlation.role${role === "entrypoint" ? "Entrypoint" : "Backend"}`)}
                      </label>
                    ))}
                  </div>
                </TableCell>
                <TableCell className="w-44 py-1.5">
                  <CellSelect ariaLabel={`${node.repo} source`} value={node.reuseScanId ?? RESCAN}
                    onChange={(v) => onState(setTopologyNodeSource(state, node.repo, v === RESCAN ? null : v))}
                    options={[
                      { value: RESCAN, label: t("scan.correlation.sourceRescan") },
                      ...whiteboxScans
                        .filter((scan) => scan.repo === node.repo && scan.status !== "running" && (scan.scan_id ?? scan.id))
                        .map((scan) => ({
                          value: scan.scan_id ?? scan.id ?? "",
                          label: <span className="font-mono text-xs">
                            {t("scan.correlation.sourceReuse")}: {scan.scan_id ?? scan.id}
                          </span>,
                        })),
                    ]} />
                </TableCell>
                <TableCell className="py-1.5 text-center">
                  <Checkbox checked={node.referenceOnly === true} aria-label={`${node.repo} reference only`}
                    onCheckedChange={(v) => onState(setTopologyReferenceOnly(state, node.repo, v === true))} />
                </TableCell>
                <TableCell className="py-1.5 text-right">
                  <Button type="button" variant="ghost" size="sm"
                    aria-label={`${t("common.delete")} ${node.repo}`} disabled={!onRemoveNode}
                    onClick={() => onRemoveNode?.(node.repo)}>{t("common.delete")}</Button>
                </TableCell>
              </TableRow>
            ))}
            <TableRow>
              <TableCell colSpan={5} className="py-2">
                <div className="flex flex-wrap items-center gap-2">
                  <div className="w-44">
                    <CellSelect ariaLabel={t("scan.correlation.topology.selectNode")} value={newNode || undefined}
                      placeholder={t("scan.correlation.topology.selectNode")}
                      onChange={(v) => setNewNode(v)}
                      options={candidates.map((repo) => ({ value: repo, label: <span className="font-mono text-xs">{repo}</span> }))} />
                  </div>
                  <Button type="button" variant="outline" size="sm"
                    disabled={!onAddNode || !newNode}
                    onClick={() => { onAddNode?.(newNode); setNewNode(""); }}>
                    {t("scan.correlation.topology.addNode")}
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </section>
      <section className="overflow-hidden rounded-lg border border-border" aria-label={t("scan.correlation.topology.edgeTable")}>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("scan.correlation.colFrom")}</TableHead>
              <TableHead>{t("scan.correlation.colTo")}</TableHead>
              <TableHead>{t("scan.correlation.protocolLabel")}</TableHead>
              <TableHead className="text-center">{t("scan.correlation.topology.enabled")}</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {draft.edges.map((edge) => (
              <TableRow key={edge.id}>
                <TableCell className="w-40 py-1.5">
                  <CellSelect ariaLabel={`${edge.id} from`} value={edge.from}
                    onChange={(e) => onState(updateTopologyEdge(state, edge.id, { from: e }))}
                    options={repos.map((node) => ({ value: node.repo, label: <span className="font-mono text-xs">{node.repo}</span> }))} />
                </TableCell>
                <TableCell className="w-40 py-1.5">
                  <CellSelect ariaLabel={`${edge.id} to`} value={edge.to}
                    onChange={(e) => onState(updateTopologyEdge(state, edge.id, { to: e }))}
                    options={repos.map((node) => ({ value: node.repo, label: <span className="font-mono text-xs">{node.repo}</span> }))} />
                </TableCell>
                <TableCell className="w-28 py-1.5">
                  <CellSelect ariaLabel="protocol" value={edge.protocol}
                    onChange={(e) => onState(updateTopologyEdge(state, edge.id, { protocol: e as never }))}
                    options={["grpc", "http", "graphql"].map((p) => ({ value: p, label: p }))} />
                </TableCell>
                <TableCell className="py-1.5 text-center">
                  <Checkbox checked={edge.enabled} aria-label={`${edge.id} enabled`}
                    onCheckedChange={(v) => onState(updateTopologyEdge(state, edge.id, { enabled: v === true }))} />
                </TableCell>
                <TableCell className="py-1.5">
                  <div className="flex justify-end gap-1">
                    <Button type="button" variant="ghost" size="sm" onClick={() => onSelectEdge(edge.id)}>{t("scan.correlation.evidence")}</Button>
                    <Button type="button" variant="ghost" size="sm" aria-label={t("scan.correlation.topology.deleteEdge")}
                      onClick={() => onState(deleteTopologyEdge(state, edge.id))}>{t("common.delete")}</Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
            <TableRow>
              <TableCell colSpan={5} className="py-2">
                <Button type="button" variant="outline" size="sm" disabled={repos.length < 2}
                  onClick={() => onState(addTopologyEdge(state, { from: repos[0].repo, to: repos[1].repo, protocol: "grpc" }))}>
                  {t("scan.correlation.topology.addEdge")}
                </Button>
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </section>
    </div>
  );
}
