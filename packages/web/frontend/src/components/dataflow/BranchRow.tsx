// 单条枝明细行（spec 2026-08-20 §5「链级⇄节点级双层语义」）。
// 链级 verdict 标签（打通 · 一路无有效防护 / 剪断 · 在 X 被拦下）+ 节点点击展开 code
// （has_code:false 降级「LLM 扫描的节点不带源码，agent 原话」）+ 与 SVG path 双向高亮联动（hover）。
// 白话文案（spec §5 白话文案表）：打通/剪断/危险点/无输入到达/节点防护 有效·被绕过。
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { DataflowBranch, DataflowNode } from "@/api/types";

export interface BranchRowProps {
  branch: DataflowBranch;
  /** 图↔行 hover 双向联动（spec §5）：SVG 枝条 hover 时由共同父级（TreeCard）回传高亮。 */
  highlighted?: boolean;
  /** hover 联动回调（null = 离开）；驱动 SVG 侧枝条同高亮（反向联动）。 */
  onHover?: (branchId: string | null) => void;
  /** 点枝条选中（spec §5）：行高亮 + 展开首个节点 code；再点枝条取消。 */
  selected?: boolean;
}

/** 链级 verdict → 链级标签文案（打通/剪断）。剪断点函数名直接进标签。 */
function chainLabel(branch: DataflowBranch, t: ReturnType<typeof useTranslation>["t"]): {
  text: string;
  tone: "vuln" | "safe" | "unknown";
} {
  if (branch.verdict === "vulnerable") {
    return { text: t("workspaceDetail.dataflow.branchVulnLabel"), tone: "vuln" };
  }
  if (branch.verdict === "safe") {
    const cutFn = cutPointFunc(branch);
    return {
      text: t("workspaceDetail.dataflow.branchSafeLabel", { cut: cutFn ?? branch.verdict_reason ?? "?" }),
      tone: "safe",
    };
  }
  return { text: t("workspaceDetail.dataflow.branchUnknownLabel"), tone: "unknown" };
}

/** 剪断点函数名（effective sanitizer 所在节点 func）。 */
function cutPointFunc(branch: DataflowBranch): string | null {
  const eff = branch.sanitizers.find((s) => s.effective === true);
  if (eff) {
    const node =
      branch.nodes.find((n) => n.line != null && eff.line != null && n.line === eff.line) ??
      branch.nodes[branch.nodes.length - 1];
    if (node?.func) return node.func;
  }
  return null;
}

/** track → 轨道标签 + 色（cyan=GitNexus / magenta=LLM，spec §5 语义色）。 */
function trackMeta(track: DataflowBranch["track"]): { label: string; color: string } {
  if (track === "gitnexus") return { label: "GN 轨", color: "hsl(var(--c-cyan))" };
  return { label: "LLM 轨", color: "hsl(var(--c-magenta))" };
}

export function BranchRow({ branch, highlighted = false, onHover, selected = false }: BranchRowProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState<string | null>(null); // 当前展开的节点 key
  const { text, tone } = chainLabel(branch, t);
  const track = trackMeta(branch.track);
  const toneCls =
    tone === "vuln" ? "border-l-[hsl(var(--c-red))]" : tone === "safe" ? "border-l-[hsl(var(--c-green))]" : "border-l-[hsl(var(--c-amber))]";

  const toggleNode = (key: string) => setExpanded((cur) => (cur === key ? null : key));

  // 点枝条选中（spec §5「点枝条展开对应明细」）：选中时展开首个节点 code（无节点枝仅高亮）。
  useEffect(() => {
    if (selected && branch.nodes.length > 0) setExpanded("n0");
  }, [selected, branch.nodes.length]);

  return (
    <div
      data-branch-row=""
      data-branch-id={branch.branch_id ?? undefined}
      data-hovered={highlighted ? "" : undefined}
      data-selected={selected ? "" : undefined}
      onMouseEnter={() => onHover?.(branch.branch_id ?? null)}
      onMouseLeave={() => onHover?.(null)}
      className={`border-l-2 ${toneCls} rounded-r-md pl-3 py-1.5 text-sm transition-colors${
        highlighted ? " branch-row-hovered" : ""
      }${selected ? " branch-row-selected" : ""}`}
    >
      {/* 链级标签 + 轨道徽章 */}
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`font-medium ${tone === "vuln" ? "text-[hsl(var(--c-red))]" : tone === "safe" ? "text-[hsl(var(--c-green))]" : "text-[hsl(var(--c-amber))]"}`}
        >
          {text}
        </span>
        <span
          className="rounded px-1.5 py-0.5 text-[10px]"
          style={{ color: track.color, border: `1px solid ${track.color}` }}
        >
          {track.label}
        </span>
        {branch.verdict_reason && tone !== "safe" && (
          <span className="text-xs text-muted-foreground">{branch.verdict_reason}</span>
        )}
      </div>

      {/* source → 节点链 → sink 摘要行 */}
      <div className="mt-1 flex flex-wrap items-center gap-1 font-mono text-xs text-muted-foreground">
        <span className="text-[hsl(var(--c-cyan))]">{branch.source.label ?? "source"}</span>
        {/* 2ND 存储中转枝标记（spec §5 白话：先存进数据库，读出来才发起请求） */}
        {branch.source.type === "storage" && (
          <span
            data-storage-relay=""
            className="rounded border px-1 py-0.5 not-italic"
            style={{ color: "hsl(var(--c-amber))", borderColor: "hsl(var(--c-amber) / 0.5)" }}
            title={t("workspaceDetail.dataflow.storageRelayFull")}
          >
            {t("workspaceDetail.dataflow.storageRelayMark")}
          </span>
        )}
        {branch.nodes.map((n, i) => (
          <span key={i} className="flex items-center gap-1">
            <span aria-hidden>→</span>
            <button
              type="button"
              data-node-toggle=""
              data-node-key={`n${i}`}
              onClick={() => toggleNode(`n${i}`)}
              className="rounded px-1 hover:bg-accent hover:text-accent-foreground"
              title={n.file != null ? `${n.file}:${n.line ?? ""}` : undefined}
            >
              {n.func ?? "step"}
              {n.line != null ? `:${n.line}` : ""}
            </button>
          </span>
        ))}
        <span aria-hidden>→</span>
        <span className="text-[hsl(var(--c-red))]">sink</span>
      </div>

      {/* 展开节点 code / 降级文案 */}
      {expanded && <NodeCode branch={branch} nodeKey={expanded} t={t} />}

      {/* sanitizer 列表（节点级防护速览） */}
      {branch.sanitizers.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
          {branch.sanitizers.map((s, i) => {
            const effLabel =
              s.effective === true
                ? t("workspaceDetail.dataflow.shieldEffective")
                : s.effective === false
                  ? t("workspaceDetail.dataflow.shieldBypassed")
                  : t("workspaceDetail.dataflow.shieldUnknown");
            const color = s.effective === true ? "hsl(var(--c-green))" : s.effective === false ? "hsl(var(--c-yellow))" : "hsl(var(--muted-foreground))";
            return (
              <span key={i} className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5">
                <span aria-hidden style={{ color }}>🛡</span>
                <span>{s.name ?? s.defense_type ?? "guard"}</span>
                <span style={{ color }}>· {effLabel}</span>
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** 展开节点的 code 或降级文案。 */
function NodeCode({
  branch,
  nodeKey,
  t,
}: {
  branch: DataflowBranch;
  nodeKey: string;
  t: ReturnType<typeof useTranslation>["t"];
}) {
  // nodeKey 形如 "n0"/"n1"，对应 branch.nodes 索引
  const idx = parseInt(nodeKey.slice(1), 10);
  const node: DataflowNode | undefined = branch.nodes[idx];
  if (!node) return null;
  return (
    <pre
      data-node-code=""
      className="mt-1 overflow-x-auto rounded bg-secondary/60 p-2 font-mono text-xs text-foreground"
    >
      {node.has_code && node.code
        ? node.code
        : t("workspaceDetail.dataflow.noCodeHint")}
    </pre>
  );
}
