// 剪枝树 SVG 组件（spec 2026-08-20 §5 视觉语言）。
// 一棵 sink 树 → 自研 SVG：水平汇聚（source 左列 → sink 右靶心）+ 列对齐
// （x = step_index × COL_W，全树统一）+ 打通枝红虚线流动 / 剪断枝绿实线至防护节点 + ✂ 残端 /
// 黄盾=绕过 / 绿盾=有效=剪断点 / 红脉动靶心或灰虚线靶心 / 同名函数青色点线弧。
// 不引可视化库（reactflow/d3 都不用）；参照 FileTree 组件惯例 + tokens.css 语义色。
import { useCallback, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { DataflowBranch, DataflowNode, DataflowTree } from "@/api/types";
import { BranchRow } from "./BranchRow";

/** 列宽：同一传播步骤节点对齐到 x = step_index × COL_W（spec §5「列对齐」）。 */
export const COL_W = 180;
/** 行高：每条枝纵向占用空间。 */
const ROW_H = 76;
const PAD_X = 0; // viewBox 左边距（step_index=0 → source 列 x=0；列对齐 x = step_index × COL_W）
const PAD_Y = 28; // viewBox 上内边距
const FOLD_THRESHOLD = 4; // 剪断枝 >4 折叠（spec §5）
const NODE_R = 11; // 节点圆半径

export interface PruningTreeFigProps {
  trees: DataflowTree[];
}

/** 节点列 x（全局列对齐）。step_index=0 → source；step_index=N → 第 N 步节点；sink 列单独。 */
function xOf(stepIndex: number): number {
  return PAD_X + stepIndex * COL_W;
}
function yOf(rowIdx: number): number {
  return PAD_Y + rowIdx * ROW_H + ROW_H / 2;
}

/** sink 列索引（= 最大中间节点数 + 1，保证 sink 在最右统一列）。 */
function sinkColIndex(tree: DataflowTree): number {
  let maxNodes = 0;
  for (const b of tree.branches) maxNodes = Math.max(maxNodes, b.nodes.length);
  return Math.max(1, maxNodes + 1);
}

/** verdict → SVG path class（打通红流动 / 剪断绿 / unknown 橙）。 */
function branchClass(verdict: DataflowBranch["verdict"]): string {
  if (verdict === "vulnerable") return "branch-vuln flow";
  if (verdict === "safe") return "branch-safe";
  return "branch-unknown";
}

/** 三次贝塞尔：从 (x1,y1) 到 (x2,y2)，控制点在中间偏移（汇入 sink 的边统一贝塞尔，spec §5）。 */
function bezier(x1: number, y1: number, x2: number, y2: number): string {
  const cx = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${cx} ${y1}, ${cx} ${y2}, ${x2} ${y2}`;
}
/** hop：相邻列节点连接（同枝内部水平传播，同 y 用直线/贝塞尔）。 */
function hop(x1: number, y1: number, x2: number, y2: number): string {
  const cx = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${cx} ${y1}, ${cx} ${y2}, ${x2} ${y2}`;
}

interface BranchLayout {
  branch: DataflowBranch;
  rowIdx: number;
}

/** 同名函数检测：树内 func 重名 → 该 func 出现于多枝。返回 Set<func 名>。 */
function sharedFuncNames(branches: DataflowBranch[]): Set<string> {
  const counts = new Map<string, number>();
  for (const b of branches) {
    for (const n of b.nodes) {
      if (n.func) counts.set(n.func, (counts.get(n.func) ?? 0) + 1);
    }
  }
  const shared = new Set<string>();
  for (const [fn, c] of counts) if (c > 1) shared.add(fn);
  return shared;
}

interface SameLineArc {
  func: string;
  from: { x: number; y: number };
  to: { x: number; y: number };
}

/** 构建同名函数弧（跨枝同名节点对，按 step 对齐成弧）。 */
function buildSameLineArcs(layouts: BranchLayout[]): SameLineArc[] {
  const shared = sharedFuncNames(layouts.map((l) => l.branch));
  if (shared.size === 0) return [];
  const map = new Map<string, { x: number; y: number }[]>();
  for (const l of layouts) {
    l.branch.nodes.forEach((n, i) => {
      if (n.func && shared.has(n.func)) {
        const key = `${n.func}#${i + 1}`;
        const arr = map.get(key) ?? [];
        arr.push({ x: xOf(i + 1), y: yOf(l.rowIdx) });
        map.set(key, arr);
      }
    });
  }
  const arcs: SameLineArc[] = [];
  for (const [key, pts] of map) {
    if (pts.length >= 2) {
      arcs.push({ func: key, from: pts[0], to: pts[1] });
    }
  }
  return arcs;
}

/** 公共函数统计（spec §5「公共函数 ⟳ N 枝经过」）：func → 经过枝数 + 剪断枝 branch_id 列表。
 *  前端按树内 nodes[].func 重名统计（无需 schema 新字段）。 */
interface PubFuncStat {
  count: number; // 经过的枝数
  cutBranches: string[]; // 该 func 所在枝里被剪断（verdict=safe）的 branch_id（hover 说明剪断哪几条枝）
}
function buildPubFuncStats(branches: DataflowBranch[]): Map<string, PubFuncStat> {
  const stats = new Map<string, PubFuncStat>();
  for (const b of branches) {
    const seen = new Set<string>(); // 同枝内同 func 只计一次
    for (const n of b.nodes) {
      const fn = n.func;
      if (!fn || seen.has(fn)) continue;
      seen.add(fn);
      const cur = stats.get(fn) ?? { count: 0, cutBranches: [] };
      cur.count += 1;
      if (b.verdict === "safe" && b.branch_id) cur.cutBranches.push(b.branch_id);
      stats.set(fn, cur);
    }
  }
  return stats;
}

export function PruningTreeFig({ trees }: PruningTreeFigProps) {
  // 跨树 source 索引（spec §5「跨树 source 提示」）：同一入口（label+entry 规范）
  // 出现在多棵树时，source tooltip 注「同一入口还流向：其它 tree 的 sink 名」。
  const crossTreeSourceTip = useMemo(() => buildCrossTreeSourceTip(trees), [trees]);
  if (trees.length === 0) return null;
  return (
    <div className="space-y-4">
      {trees.map((tree) => (
        <TreeCard key={tree.tree_id} tree={tree} crossTreeSourceTip={crossTreeSourceTip} />
      ))}
    </div>
  );
}

/** 跨树 source 索引：key = `${label}|${entry}` → 该入口出现在哪些树的 sink 名列表。
 *  返回函数 (source, currentTreeId) → 跨树提示字符串（无跨树时返回 null）。
 *  tooltip 排除当前树，只列「其它 tree 的 sink 名」（spec §5）。 */
function buildCrossTreeSourceTip(
  trees: DataflowTree[],
): (source: DataflowBranch["source"], currentTreeId: string) => string | null {
  const index = new Map<string, { treeId: string; sink: string }[]>();
  for (const tree of trees) {
    for (const branch of tree.branches) {
      const key = sourceKey(branch.source);
      if (!key) continue;
      const entry = index.get(key) ?? [];
      const sink = tree.sink.label ?? tree.tree_id;
      if (!entry.some((e) => e.treeId === tree.tree_id)) {
        entry.push({ treeId: tree.tree_id, sink });
      }
      index.set(key, entry);
    }
  }
  return (source: DataflowBranch["source"], currentTreeId: string) => {
    const key = sourceKey(source);
    if (!key) return null;
    const entries = index.get(key);
    if (!entries || entries.length < 2) return null; // 仅出现在一棵树 → 无跨树提示
    // 排除当前树，只列其它树的 sink 名
    const otherSinks = entries.filter((e) => e.treeId !== currentTreeId).map((e) => e.sink);
    if (otherSinks.length === 0) return null;
    return otherSinks.join(" / ");
  };
}

/** source 规范化 key（label + entry；二者皆空 → null，不索引）。 */
function sourceKey(s: DataflowBranch["source"]): string | null {
  const label = (s.label ?? "").trim();
  const entry = (s.entry ?? "").trim();
  if (!label && !entry) return null;
  return `${label}|${entry}`;
}

/** 单棵树卡：树头徽章 + SVG 剪枝树（缩放平移容器）+ 枝条明细列表。
 *  图↔行交互（spec §5「交互」段）：TreeCard 是 SVG 枝条与明细行的共同父级，
 *  联动 state 提升至此——hover 任一侧（path 或 BranchRow）→ 两侧同高亮（双向）；
 *  点枝条 → 选中对应明细行（高亮 + 展开首个节点 code，再点取消）。 */
function TreeCard({
  tree,
  crossTreeSourceTip,
}: {
  tree: DataflowTree;
  crossTreeSourceTip: (source: DataflowBranch["source"], currentTreeId: string) => string | null;
}) {
  const { t } = useTranslation();
  // hover 联动：当前 hover 的 branch_id（null=无）；由 SVG 枝条与 BranchRow 双向写入。
  const [hoveredBranch, setHoveredBranch] = useState<string | null>(null);
  // 点枝条选中：当前选中的 branch_id（null=无；再点同一枝取消）。
  const [selectedBranch, setSelectedBranch] = useState<string | null>(null);
  const handleBranchHover = useCallback((id: string | null) => setHoveredBranch(id), []);
  const handleBranchSelect = useCallback(
    (id: string) => setSelectedBranch((cur) => (cur === id ? null : id)),
    [],
  );
  const sinkCol = sinkColIndex(tree);
  const sinkX = xOf(sinkCol);
  const vulnCount = tree.branches.filter((b) => b.verdict === "vulnerable").length;
  const safeCount = tree.branches.filter((b) => b.verdict === "safe").length;
  const hasVuln = vulnCount > 0 || tree.findings.length > 0;

  // 枝条布局：打通/unknown 枝全部展开在前；剪断枝 >FOLD_THRESHOLD 折叠
  const { layouts, foldedSafeCount } = useMemo(() => {
    const vulnBranches = tree.branches.filter((b) => b.verdict === "vulnerable" || b.verdict === "unknown");
    const safeBranches = tree.branches.filter((b) => b.verdict === "safe");
    const fold = safeBranches.length > FOLD_THRESHOLD;
    const shownSafe = fold ? safeBranches.slice(0, FOLD_THRESHOLD) : safeBranches;
    const folded = fold ? safeBranches.length - FOLD_THRESHOLD : 0;
    const all = [...vulnBranches, ...shownSafe];
    const ls: BranchLayout[] = all.map((branch, i) => ({ branch, rowIdx: i }));
    return { layouts: ls, foldedSafeCount: folded };
  }, [tree.branches]);

  const rows = layouts.length + (foldedSafeCount > 0 ? 1 : 0);
  // sink 汇聚点 Y（所有行中线，让多枝向中汇聚；单枝时就在该枝中线）
  const sinkY = layouts.length > 1
    ? PAD_Y + (rows - 1) * ROW_H / 2 + ROW_H / 2
    : yOf(0);
  const svgWidth = sinkX + COL_W * 0.8 + PAD_X;
  const svgHeight = PAD_Y * 2 + rows * ROW_H;
  const sharedArcs = useMemo(() => buildSameLineArcs(layouts), [layouts]);
  // 公共函数统计：func → { count, cutBranches }（spec §5「公共函数 ⟳ N 枝经过」）
  const pubFuncStats = useMemo(() => buildPubFuncStats(layouts.map((l) => l.branch)), [layouts]);

  return (
    <section
      className="rounded-lg border border-border bg-card p-4 shadow-card"
      data-testid="pruning-tree-card"
      data-tree-id={tree.tree_id}
    >
      <TreeHeader tree={tree} t={t} vulnCount={vulnCount} safeCount={safeCount} hasVuln={hasVuln} />
      <ZoomViewport maxHeight={520}>
        <svg
          width="100%"
          viewBox={`${-PAD_X} 0 ${svgWidth + PAD_X} ${svgHeight}`}
          role="img"
          aria-label={t("workspaceDetail.dataflow.pruningTreeAria", {
            sink: tree.sink.label ?? "sink",
            vuln: vulnCount,
            safe: safeCount,
          })}
        >
          {layouts.map((layout) => (
            <BranchPath
              key={layout.branch.branch_id ?? layout.rowIdx}
              layout={layout}
              sinkCol={sinkCol}
              sinkX={sinkX}
              sinkY={sinkY}
              pubFuncStats={pubFuncStats}
              crossTreeSourceTip={crossTreeSourceTip}
              t={t}
              treeId={tree.tree_id}
              hovered={!!layout.branch.branch_id && hoveredBranch === layout.branch.branch_id}
              selected={!!layout.branch.branch_id && selectedBranch === layout.branch.branch_id}
              onHover={handleBranchHover}
              onSelect={handleBranchSelect}
            />
          ))}
          {foldedSafeCount > 0 && (
            <FoldedSafeRow rowIdx={layouts.length} count={foldedSafeCount} sinkCol={sinkCol} t={t} />
          )}
          {sharedArcs.map((arc, i) => (
            <SameLineArcView key={i} arc={arc} t={t} />
          ))}
          {/* sink 靶心：有打通枝 → 红脉动圆环；safe-only → 灰虚线圆环（无输入到达） */}
          <SinkTarget
            x={sinkX}
            y={sinkY}
            hasVuln={hasVuln}
            label={tree.sink.label ?? "sink"}
            t={t}
          />
        </svg>
      </ZoomViewport>
      {/* 枝条明细列表（与 SVG path 双向高亮联动；点枝条选中展开） */}
      <div className="mt-3">
        {tree.branches.map((b) => (
          <BranchRow
            key={b.branch_id ?? b.source.label}
            branch={b}
            highlighted={!!b.branch_id && hoveredBranch === b.branch_id}
            selected={!!b.branch_id && selectedBranch === b.branch_id}
            onHover={handleBranchHover}
          />
        ))}
      </div>
    </section>
  );
}

/** 树头徽章：sink 名 + file:line + rule_id/class + finding IDs + 红绿迷你比例条。 */
function TreeHeader({
  tree,
  t,
  vulnCount,
  safeCount,
  hasVuln,
}: {
  tree: DataflowTree;
  t: ReturnType<typeof useTranslation>["t"];
  vulnCount: number;
  safeCount: number;
  hasVuln: boolean;
}) {
  const findingIds = tree.findings.map((f) => f.id).filter(Boolean).join(", ");
  const total = Math.max(1, vulnCount + safeCount);
  const vulnPct = (vulnCount / total) * 100;
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
      <span
        className={`inline-flex size-2.5 rounded-full ${hasVuln ? "bg-[hsl(var(--c-red))]" : "bg-[hsl(var(--c-green))]"}`}
        aria-hidden
      />
      <span className="font-medium">{tree.sink.label ?? t("workspaceDetail.dataflow.sink")}</span>
      {tree.sink.file && (
        <span className="font-mono text-xs text-muted-foreground">
          {tree.sink.file}
          {tree.sink.line != null ? `:${tree.sink.line}` : ""}
        </span>
      )}
      {tree.sink.rule_id && (
        <span className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
          {tree.sink.rule_id}
        </span>
      )}
      {tree.sink.category && (
        <span className="rounded bg-secondary px-1.5 py-0.5 text-[10px] text-muted-foreground">
          {tree.sink.category}
        </span>
      )}
      {findingIds && (
        <span className="font-mono text-xs text-muted-foreground">{findingIds}</span>
      )}
      <span className="ml-auto flex items-center gap-1.5">
        <span className="text-xs text-muted-foreground">
          {t("workspaceDetail.dataflow.minibar", { vuln: vulnCount, safe: safeCount })}
        </span>
        <span
          className="inline-flex h-2 w-16 overflow-hidden rounded-full border border-border"
          role="img"
          aria-label={t("workspaceDetail.dataflow.minibarAria", { vuln: vulnCount, safe: safeCount })}
        >
          <span className="bg-[hsl(var(--c-red))]" style={{ width: `${vulnPct}%` }} />
          <span className="bg-[hsl(var(--c-green))]" style={{ width: `${100 - vulnPct}%` }} />
        </span>
      </span>
    </div>
  );
}

/** 单枝 SVG：source pill → 节点链 → 防护盾 → 汇入 sink 或剪断残端。
 *  交互（spec §5）：hover 枝条（g 级，含节点/pill）→ onHover 联动高亮明细行；
 *  点击枝条 → onSelect 选中对应明细行（展开首个节点 code）。 */
function BranchPath({
  layout,
  sinkCol: _sinkCol,
  sinkX,
  sinkY,
  pubFuncStats,
  crossTreeSourceTip,
  t,
  treeId,
  hovered,
  selected,
  onHover,
  onSelect,
}: {
  layout: BranchLayout;
  sinkCol: number;
  sinkX: number;
  sinkY: number;
  pubFuncStats: Map<string, PubFuncStat>;
  crossTreeSourceTip: (source: DataflowBranch["source"], currentTreeId: string) => string | null;
  t: ReturnType<typeof useTranslation>["t"];
  treeId: string;
  hovered: boolean;
  selected: boolean;
  onHover: (branchId: string | null) => void;
  onSelect: (branchId: string) => void;
}) {
  const { branch, rowIdx } = layout;
  const y = yOf(rowIdx);
  const verdict = branch.verdict;
  const cls = branchClass(verdict);
  const reachesSink = verdict === "vulnerable" || verdict === "unknown";
  const sourceX = xOf(0);

  // 剪断点 step（effective sanitizer 所在节点 step）
  const effSan = branch.sanitizers.find((s) => s.effective === true);
  let cutStep = -1;
  if (effSan && verdict === "safe") {
    const idx = branch.nodes.findIndex((n) => n.line != null && effSan.line != null && n.line === effSan.line);
    cutStep = idx >= 0 ? idx + 1 : branch.nodes.length;
  }

  // 主 path 终点：打通枝 → sink 靶心；剪断枝 → 剪断点节点
  const endX = reachesSink ? sinkX : (cutStep > 0 ? xOf(cutStep) : xOf(branch.nodes.length));
  const endY = reachesSink ? sinkY : y;

  // 构造 path d：source → 节点链 → 终点（同枝节点同 y；最后一段汇入 sink 用贝塞尔）
  const pts: { x: number; y: number }[] = [{ x: sourceX, y }];
  branch.nodes.forEach((_, i) => pts.push({ x: xOf(i + 1), y }));
  pts.push({ x: endX, y: endY });
  let d = "";
  for (let i = 1; i < pts.length; i++) {
    const prev = pts[i - 1];
    const cur = pts[i];
    // 最后一段若汇入 sink（y 不同）用贝塞尔；否则同 y 直线
    if (i === pts.length - 1 && reachesSink && cur.y !== prev.y) {
      d += " " + bezier(prev.x, prev.y, cur.x, cur.y).replace("M", "L");
    } else {
      const cx = (prev.x + cur.x) / 2;
      d += ` M ${prev.x} ${prev.y} C ${cx} ${prev.y}, ${cx} ${cur.y}, ${cur.x} ${cur.y}`;
    }
  }

  // 联动高亮 class（hovered=hover 查看中 / selected=点选；CSS 加粗提亮，见 tokens.css）
  const hl = hovered ? " hovered" : selected ? " selected" : "";
  return (
    <g
      data-branch={verdict}
      data-branch-id={branch.branch_id ?? undefined}
      className={cls + hl}
      data-hovered={hovered ? "" : undefined}
      data-selected={selected ? "" : undefined}
      onMouseEnter={() => onHover(branch.branch_id ?? null)}
      onMouseLeave={() => onHover(null)}
      onClick={() => branch.branch_id && onSelect(branch.branch_id)}
    >
      <path d={d.trim()} className={cls + hl} data-branch={verdict} />
      {/* 剪断枝残端：从剪断点到 sink 方向渐隐虚线（不到 sink） */}
      {!reachesSink && cutStep > 0 && (
        <path
          d={hop(xOf(cutStep), y, sinkX - COL_W * 0.35, y)}
          className="branch-remnant"
          data-branch={verdict}
          data-remnant=""
        />
      )}
      {/* source 青色 pill（label + type + METHOD/route，跨树 tooltip） */}
      <SourcePill
        x={sourceX}
        y={y}
        source={branch.source}
        crossTreeTip={crossTreeSourceTip(branch.source, treeId)}
        t={t}
      />
      {/* 节点 + 防护盾 + 公共函数下标 */}
      {branch.nodes.map((node, i) => {
        const step = i + 1;
        const isCut = !reachesSink && step === cutStep;
        const san = branch.sanitizers.find((s) => s.line != null && node.line === s.line);
        const hasShield = !!san;
        const shieldEff = san?.effective === true;
        const shieldBypass = san?.effective === false;
        return (
          <NodeView
            key={i}
            x={xOf(step)}
            y={y}
            node={node}
            step={step}
            isVuln={reachesSink}
            isCut={isCut}
            hasShield={hasShield}
            shieldEff={shieldEff}
            shieldBypass={shieldBypass}
            pubFuncStat={node.func ? pubFuncStats.get(node.func) : undefined}
            t={t}
          />
        );
      })}
      {/* 剪刀标记（剪断点） */}
      {!reachesSink && cutStep > 0 && (
        <text x={xOf(cutStep) + NODE_R + 4} y={y + 4} className="scissors-mark" data-scissors="">
          ✂
        </text>
      )}
    </g>
  );
}

/** source 青色 pill（step_index=0，列对齐 x=0）。
 *  spec §5：青色 pill 显示「参数名 + type + METHOD /route」。
 *  2ND 存储中转枝（source.type === "storage"）：pill 下方加琥珀色「⟳ 存储中转」标记，
 *  tooltip 用 spec §5 白话「经过存储中转：先存进数据库，读出来才发起请求」。
 *  跨树 source 提示（spec §5「跨树 source 提示」）：同一入口（label+entry）出现在多棵树时，
 *  tooltip 注「同一入口还流向：[其它 tree 的 sink 名]」，避免误读为重复数据（与存储提示并存）。 */
function SourcePill({
  x,
  y,
  source,
  crossTreeTip,
  t,
}: {
  x: number;
  y: number;
  source: DataflowBranch["source"];
  crossTreeTip: string | null;
  t: ReturnType<typeof useTranslation>["t"];
}) {
  const label = source.label ?? "source";
  const isStorage = source.type === "storage";
  // 副信息行：METHOD /route（storage 的 type 不直译进副行——用白话标记行承载）
  const metaParts = [isStorage ? null : source.type, source.entry].filter(Boolean);
  const hasMeta = metaParts.length > 0;
  // pill 主文本：label
  const w = Math.min(COL_W - 8, Math.max(56, label.length * 6 + 16));
  // tooltip：存储中转白话（2ND 枝）+ 跨树提示（并存拼接）；都无 → source 基本描述
  const tipParts: string[] = [];
  if (isStorage) tipParts.push(t("workspaceDetail.dataflow.storageRelayFull"));
  if (crossTreeTip) tipParts.push(t("workspaceDetail.dataflow.crossTreeTooltip", { sinks: crossTreeTip }));
  const tooltip =
    tipParts.length > 0
      ? tipParts.join(" ｜ ")
      : [source.label, source.type, source.entry].filter(Boolean).join(" · ") || "source";
  return (
    /* transform 平移局部坐标系到 (x,y)：data-tooltip 的 CSS ::after 浮层在局部原点
       渲染（SVG 伪元素无 CSS 盒定位），带 transform 即浮在 pill 自身位置。 */
    <g
      data-source=""
      data-node="0"
      x={x}
      transform={`translate(${x} ${y})`}
      className="source-pill"
      data-tooltip={tooltip}
    >
      <rect x={-6} y={-12} width={w} height={24} rx={12} className="source-pill" />
      <text x={2} y={4} className="source-pill-txt" textAnchor="start">
        {label}
      </text>
      {/* 副信息行：type · METHOD /route（spec §5 source 行要求） */}
      {hasMeta && (
        <text x={2} y={15} className="source-meta-txt" textAnchor="start">
          {metaParts.join(" · ")}
        </text>
      )}
      {/* 存储中转白话标记（2ND 枝 source.type=storage）：琥珀色，tooltip 含完整白话 */}
      {isStorage && (
        <text x={2} y={27} className="storage-relay-txt" textAnchor="start" data-storage-relay="">
          {t("workspaceDetail.dataflow.storageRelayMark")}
        </text>
      )}
    </g>
  );
}

/** 节点：圆 + 函数名 + 防护盾（黄=绕过 / 绿=有效=剪断点）+ 剪刀（剪断点）。
 *  data-node = step_index（1-based 中间节点；source 是 step 0 在 SourcePill）。
 *  公共函数下标（spec §5「公共函数 ⟳ N 枝经过」）：func 经多枝共用时，
 *  节点下方加「⟳ 公共函数 · N 枝经过」（N=树内 func 重名计数，前端自算），
 *  hover tooltip 说明剪断了哪几条枝。 */
function NodeView({
  x,
  y,
  node,
  step,
  isVuln,
  isCut,
  hasShield,
  shieldEff,
  shieldBypass,
  pubFuncStat,
  t,
}: {
  x: number;
  y: number;
  node: DataflowNode;
  step: number;
  isVuln: boolean;
  isCut: boolean;
  hasShield: boolean;
  shieldEff: boolean;
  shieldBypass: boolean;
  pubFuncStat?: PubFuncStat;
  t: ReturnType<typeof useTranslation>["t"];
}) {
  const boxClass = isVuln ? "node-box-vuln" : isCut ? "node-box-safe" : "node-box";
  // 公共函数下标：仅当该 func 经 >1 枝时显示（spec §5）
  const isPubFunc = !!pubFuncStat && pubFuncStat.count > 1;
  const pubTooltip = isPubFunc
    ? pubFuncStat!.cutBranches.length > 0
      ? t("workspaceDetail.dataflow.pubFuncTooltip", {
          count: pubFuncStat!.count,
          cut: pubFuncStat!.cutBranches.join(", "),
        })
      : t("workspaceDetail.dataflow.pubFuncTooltipNone", { count: pubFuncStat!.count })
    : undefined;
  return (
    /* transform 平移局部坐标系到 (x,y)：data-tooltip 的 CSS ::after 浮层在局部原点
       渲染（SVG 伪元素无 CSS 盒定位），带 transform 即浮在节点自身位置。 */
    <g data-node={step} x={x} transform={`translate(${x} ${y})`} data-tooltip={pubTooltip}>
      <circle cx={0} cy={0} r={NODE_R} className={boxClass} />
      {/* 防护盾（外圈）：绿=有效 / 黄=绕过 */}
      {hasShield && (shieldEff || shieldBypass) && (
        <circle
          cx={0}
          cy={0}
          r={NODE_R + 5}
          className={shieldEff ? "shield-green" : "shield-yellow"}
          fill="none"
        />
      )}
      {/* 剪刀（剪断点节点） */}
      {isCut && (
        <text x={NODE_R + 4} y={4} className="scissors-mark" data-scissors="">
          ✂
        </text>
      )}
      {/* 函数名 + line 标签 */}
      <text x={0} y={NODE_R + 14} className="fill-[hsl(var(--foreground))]" fontSize={10} textAnchor="middle">
        {node.func ?? "?"}
      </text>
      {node.line != null && (
        <text x={0} y={NODE_R + 26} className="fill-[hsl(var(--muted-foreground))]" fontSize={9} textAnchor="middle">
          :{node.line}
        </text>
      )}
      {/* 公共函数下标（spec §5）：⟳ 公共函数 · N 枝经过 */}
      {isPubFunc && (
        <text
          x={0}
          y={NODE_R + 38}
          className="pubfunc-sub"
          textAnchor="middle"
          data-pubfunc=""
        >
          {t("workspaceDetail.dataflow.pubFuncSub", { count: pubFuncStat!.count })}
        </text>
      )}
    </g>
  );
}

/** 折叠剪断枝行：「+N 条枝被剪断」。 */
function FoldedSafeRow({
  rowIdx,
  count,
  sinkCol,
  t,
}: {
  rowIdx: number;
  count: number;
  sinkCol: number;
  t: ReturnType<typeof useTranslation>["t"];
}) {
  const y = yOf(rowIdx);
  const x1 = xOf(0);
  const x2 = xOf(sinkCol) - COL_W * 0.35;
  return (
    <g data-collapsed-safe="">
      <path d={hop(x1, y, x2, y)} className="folded-safe" />
      <text x={(x1 + x2) / 2} y={y - 6} className="fill-[hsl(var(--c-green))]" fontSize={11} textAnchor="middle">
        {t("workspaceDetail.dataflow.foldedSafe", { count })}
      </text>
    </g>
  );
}

/** 同一函数青色点线弧 + 标注（spec §5「同一函数虚线 ⟳」：不合并节点）。
 *  与「公共函数 ⟳ N 枝经过」节点下标是 spec 表格两行独立元素：弧=跨枝同一性（点线连同名节点），
 *  下标=N 枝经过计数（在 NodeView 节点下方）。本组件只画弧 + 「⟳ 同一函数」小标。 */
function SameLineArcView({ arc, t }: { arc: SameLineArc; t: ReturnType<typeof useTranslation>["t"] }) {
  const midX = (arc.from.x + arc.to.x) / 2;
  const midY = Math.min(arc.from.y, arc.to.y) - 18;
  const d = `M ${arc.from.x} ${arc.from.y} Q ${midX} ${midY}, ${arc.to.x} ${arc.to.y}`;
  return (
    <g data-sameline="" className="sameline">
      <path d={d} className="sameline" />
      <text x={midX} y={midY - 2} className="sameline-txt" textAnchor="middle" data-sameline-label="">
        {t("workspaceDetail.dataflow.samelineLabel")}
      </text>
    </g>
  );
}

/** sink 靶心：有打通枝 → 红实线圆环 + 脉动；safe-only → 灰虚线圆环。
 *  灰靶心带「无输入到达」标注（spec §5 白话表：sink 无枝到达 = 无输入到达，禁「未被触及」）。 */
function SinkTarget({
  x,
  y,
  hasVuln,
  label,
  t,
}: {
  x: number;
  y: number;
  hasVuln: boolean;
  label: string;
  t: ReturnType<typeof useTranslation>["t"];
}) {
  const sinkCls = hasVuln ? "sink-pulse" : "sink-idle";
  const noInputTip = hasVuln ? undefined : t("workspaceDetail.dataflow.sinkNoInput");
  return (
    <g
      data-sink-target={hasVuln ? "vuln" : "safe"}
      transform={`translate(${x} ${y})`}
      className={sinkCls}
      data-tooltip={noInputTip}
    >
      {/* 原生 SVG tooltip（hover 教读图） */}
      {noInputTip && <title>{noInputTip}</title>}
      <circle r={16} className={sinkCls} />
      <circle r={6} fill={hasVuln ? "hsl(var(--c-red))" : "hsl(var(--muted-foreground))"} opacity={hasVuln ? 0.8 : 0.4} />
      <text x={0} y={30} className="sink-label" textAnchor="middle">
        {label}
      </text>
      {!hasVuln && (
        <text x={0} y={44} className="sink-noinput-txt" textAnchor="middle" data-sink-noinput="">
          {noInputTip}
        </text>
      )}
    </g>
  );
}

/** 缩放平移容器：限高 + wheel 缩放（鼠标锚点）+ 拖拽平移 + 重置/百分比控件。 */
function ZoomViewport({ children, maxHeight }: { children: React.ReactNode; maxHeight: number }) {
  const [scale, setScale] = useState(1);
  const [tx, setTx] = useState(0);
  const [ty, setTy] = useState(0);
  const dragRef = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);

  const onWheel = useCallback((e: React.WheelEvent) => {
    if (e.cancelable) e.preventDefault();
    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    setScale((s) => Math.min(3, Math.max(0.3, s * delta)));
  }, []);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    dragRef.current = { x: e.clientX, y: e.clientY, tx, ty };
  }, [tx, ty]);

  const onMouseMove = useCallback((e: React.MouseEvent) => {
    if (!dragRef.current) return;
    setTx(dragRef.current.tx + (e.clientX - dragRef.current.x));
    setTy(dragRef.current.ty + (e.clientY - dragRef.current.y));
  }, []);

  const endDrag = useCallback(() => {
    dragRef.current = null;
  }, []);

  const reset = useCallback(() => {
    setScale(1);
    setTx(0);
    setTy(0);
  }, []);

  return (
    <div
      data-viewport=""
      data-max-height={String(maxHeight)}
      style={{ maxHeight, overflow: "auto", position: "relative", cursor: "grab" }}
      onWheel={onWheel}
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMove}
      onMouseUp={endDrag}
      onMouseLeave={endDrag}
    >
      <div style={{ transform: `translate(${tx}px, ${ty}px) scale(${scale})`, transformOrigin: "0 0" }}>
        {children}
      </div>
      <button
        type="button"
        onClick={reset}
        data-zoom-reset=""
        className="absolute right-2 top-2 z-10 rounded border border-border bg-card px-2 py-0.5 text-xs text-muted-foreground hover:text-primary"
        title="reset"
      >
        {Math.round(scale * 100)}%
      </button>
    </div>
  );
}
