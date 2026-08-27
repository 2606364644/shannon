// 剪枝树 SVG 组件（spec 2026-08-20 §5 视觉语言）。
// 一棵 sink 树 → 自研 SVG：水平汇聚（source 左列 → sink 右靶心）+ 列对齐
// （x = step_index × COL_W，全树统一）+ 打通枝红虚线流动 / 剪断枝绿实线至防护节点 + ✂ 残端 /
// 黄盾=绕过 / 绿盾=有效=剪断点 / 红脉动靶心或灰虚线靶心 / 同名函数青色点线弧。
// 不引可视化库（reactflow/d3 都不用）；参照 FileTree 组件惯例 + tokens.css 语义色。
import { cloneElement, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { DataflowBranch, DataflowNode, DataflowTree } from "@/api/types";
import { BranchRow } from "./BranchRow";

/** 列宽：同一传播步骤节点对齐到 x = step_index × COL_W（spec §5「列对齐」）。 */
export const COL_W = 180;
/** 行高：每条枝纵向占用空间（88 = pill ≤34 + 节点标签两行 + 公共函数下标 + 下一行 pill 顶间隙）。 */
export const ROW_H = 88;
/** 有副行（type·entry / 存储中转标记）时 source pill 半高（单行 pill 半高 11）。 */
export const PILL_HALF_H_MAX = 17;
/** 节点标签首行基线（相对行中心；须 > PILL_HALF_H_MAX + 字形高 8 + 间隙 3——
 *  pill 右缘与 step-1 标签左缘横向重叠 ~58px 是列宽 180 下的既定几何，
 *  纵向带必须完全错开，否则副行文字与节点标签互叠（2026-08-21 真实数据重叠）。 */
export const NODE_LABEL_Y1 = 28;
const PAD_X = 16; // viewBox 左边距（容纳 source pill 左缘 -6 与盾外圈，不裁切）
const PAD_Y = 28; // viewBox 上内边距
const FOLD_THRESHOLD = 4; // 剪断枝 >4 折叠（spec §5）
const NODE_R = 11; // 节点圆半径

export interface PruningTreeFigProps {
  trees: DataflowTree[];
}

/** 节点列 x（全局列对齐）。step_index=0 → source；step_index=N → 第 N 步节点；sink 列单独。
 *  列对齐语义基于「列序号 × COL_W」的相对结构，PAD_X 只作 viewBox 整体左边距（不进 xOf，
 *  保持 data-node 列位断点语义与 xOf(step)=step×COL_W 的可读性）。 */
function xOf(stepIndex: number): number {
  return stepIndex * COL_W;
}
function yOf(rowIdx: number): number {
  return PAD_Y + rowIdx * ROW_H + ROW_H / 2;
}

/** 估算文本显示宽（px，fontSize≈10：全角/中文 10px、半角 5.6px）——SVG text 无自动换行/省略，
 *  真实数据 label（LLM dataflow_steps 的自然语言描述、长函数名）必须按列宽预算手工截断，
 *  否则溢出到邻列与相邻节点文字重叠（2026-08-21 真实数据布局回归）。 */
function textWidthPx(s: string): number {
  let w = 0;
  for (const ch of s) w += ch.charCodeAt(0) > 0xff ? 10 : 5.6;
  return w;
}
/** 按像素预算截断标签：超出加「…」保留前缀（全名进 <title>）。 */
function fitLabel(s: string, budgetPx: number): string {
  if (textWidthPx(s) <= budgetPx) return s;
  const ellipsis = 10;
  let w = 0;
  let out = "";
  for (const ch of s) {
    const cw = ch.charCodeAt(0) > 0xff ? 10 : 5.6;
    if (w + cw > budgetPx - ellipsis) return out + "…";
    w += cw;
    out += ch;
  }
  return out;
}

/** 按像素预算把标签拆成 ≤2 行（真实数据 func 是 40-70 字符自然语言描述，
 *  单行一刀切到 ~26 半角信息量暴跌——两行预算翻倍且严格列内，相邻列不叠）。
 *  一行装得下 → [原文]；两行也装不下 → 第二行尾「…」（全名进 <title>）。 */
function fitLabelTwoLines(s: string, budgetPx: number): string[] {
  if (textWidthPx(s) <= budgetPx) return [s];
  // 逐字符贪心装第一行（不预算 …——两行兜底会加）
  let w = 0;
  let cut = 0;
  for (const ch of s) {
    const cw = ch.charCodeAt(0) > 0xff ? 10 : 5.6;
    if (w + cw > budgetPx) break;
    w += cw;
    cut++;
  }
  const l1 = s.slice(0, cut);
  const rest = s.slice(cut);
  const l2 = fitLabel(rest, budgetPx);
  // 第一行尽量吃满但避免行尾标点悬挂：OK 简化，直接返回
  return [l1, l2];
}

/** sink 列索引（= 最大中间节点数 + 1，保证 sink 在最右统一列）。 */
function sinkColIndex(tree: DataflowTree): number {
  let maxNodes = 0;
  for (const b of tree.branches) maxNodes = Math.max(maxNodes, b.nodes.length);
  return Math.max(1, maxNodes + 1);
}

/** verdict → SVG path class（打通红流动 / 剪断绿 / unknown 橙）。 */
function branchClass(verdict: DataflowBranch["verdict"]): string {
  // 打通枝不再常驻 .flow（2026-08-27 动效预算 spec §3）：流动动画由 tokens.css
  // 在 hovered/selected/直接 hover 时触发——静态红虚线已表达 verdict 语义
  if (verdict === "vulnerable") return "branch-vuln";
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

/** 缩放锚定（2026-08-26 UX 修复）：保持光标（或视口中心）下的内容不动，反推新 scroll。
 *  内容坐标 cx = (curScroll + viewportOffset) / fromScale；新 scroll = cx × toScale − viewportOffset。
 *  不补偿时光标下的点缩放后漂移（scale 改 svg width，视口锚死在左上角），用户要反复拖回。 */
export function nextScrollForZoom(
  curScroll: number,
  viewportOffset: number,
  fromScale: number,
  toScale: number,
): number {
  const content = (curScroll + viewportOffset) / fromScale;
  return Math.max(0, content * toScale - viewportOffset);
}

/** verdict → 链级短词（键盘 aria 用：打通/剪断/未判定）。 */
function verdictShort(
  verdict: DataflowBranch["verdict"],
  t: ReturnType<typeof useTranslation>["t"],
): string {
  if (verdict === "vulnerable") return t("workspaceDetail.dataflow.branchShortVuln");
  if (verdict === "safe") return t("workspaceDetail.dataflow.branchShortSafe");
  return t("workspaceDetail.dataflow.branchShortUnknown");
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

/** 构建同名函数弧（跨枝同名节点对）。按 func 分组（不带 step 序号——同名函数在不同枝
 *  出现在不同深度是真实数据常态，按 `func#step` 分组会让绝大多数同名对连不上弧）；
 *  多点位时链式连相邻对（点 1↔2、2↔3……）。 */
function buildSameLineArcs(layouts: BranchLayout[]): SameLineArc[] {
  const shared = sharedFuncNames(layouts.map((l) => l.branch));
  if (shared.size === 0) return [];
  const map = new Map<string, { x: number; y: number }[]>();
  for (const l of layouts) {
    l.branch.nodes.forEach((n, i) => {
      if (n.func && shared.has(n.func)) {
        const arr = map.get(n.func) ?? [];
        arr.push({ x: xOf(i + 1), y: yOf(l.rowIdx) });
        map.set(n.func, arr);
      }
    });
  }
  const arcs: SameLineArc[] = [];
  for (const [fn, pts] of map) {
    for (let i = 1; i < pts.length; i++) {
      arcs.push({ func: fn, from: pts[i - 1], to: pts[i] });
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
  // 折叠行展开态（2026-08-26 折叠枝联动修复）：点「+N 条枝被剪断」行展开全部剪断枝，再点收起。
  const [foldExpanded, setFoldExpanded] = useState(false);
  const handleBranchHover = useCallback((id: string | null) => setHoveredBranch(id), []);
  const handleBranchSelect = useCallback(
    (id: string) => setSelectedBranch((cur) => (cur === id ? null : id)),
    [],
  );
  const sinkCol = sinkColIndex(tree);
  const sinkX = xOf(sinkCol);
  const vulnCount = tree.branches.filter((b) => b.verdict === "vulnerable").length;
  const safeCount = tree.branches.filter((b) => b.verdict === "safe").length;
  const unknownCount = tree.branches.filter((b) => b.verdict === "unknown").length;
  const hasVuln = vulnCount > 0 || tree.findings.length > 0;

  // 枝条布局：打通/unknown 枝全部展开在前；剪断枝 >FOLD_THRESHOLD 折叠（可点折叠行展开）
  const { layouts, foldedIds, hasFold } = useMemo(() => {
    const vulnBranches = tree.branches.filter((b) => b.verdict === "vulnerable" || b.verdict === "unknown");
    const safeBranches = tree.branches.filter((b) => b.verdict === "safe");
    const hasFold = safeBranches.length > FOLD_THRESHOLD;
    const fold = hasFold && !foldExpanded;
    const shownSafe = fold ? safeBranches.slice(0, FOLD_THRESHOLD) : safeBranches;
    // 被折叠枝 id（hover 明细行时折叠行联动高亮，图上反馈「该枝在折叠批次里」）
    const ids = fold
      ? safeBranches.slice(FOLD_THRESHOLD).map((b) => b.branch_id).filter((x): x is string => !!x)
      : [];
    const all = [...vulnBranches, ...shownSafe];
    const ls: BranchLayout[] = all.map((branch, i) => ({ branch, rowIdx: i }));
    return { layouts: ls, foldedIds: ids, hasFold };
  }, [tree.branches, foldExpanded]);

  const rows = layouts.length + (hasFold ? 1 : 0);
  // sink 汇聚点 Y（所有行中线，让多枝向中汇聚；单枝时就在该枝中线）
  const sinkY = layouts.length > 1
    ? PAD_Y + (rows - 1) * ROW_H / 2 + ROW_H / 2
    : yOf(0);
  // 图区尺寸：sink 右边距一整列（靶心 r16 + 截断 label + 余量）；
  // svg 用像素宽（非 100%）——深链树（多列）不再被等比压进容器致字号缩到不可读，
  // 横向看全貌交给 ZoomViewport 的滚动/缩放平移（spec §5「图区缩放平移」）。
  const svgWidth = sinkX + COL_W;
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
      <TreeHeader
        tree={tree}
        t={t}
        vulnCount={vulnCount}
        safeCount={safeCount}
        unknownCount={unknownCount}
        hasVuln={hasVuln}
      />
      <ZoomViewport maxHeight={520}>
        <svg
          width={svgWidth + PAD_X}
          viewBox={`${-PAD_X} 0 ${svgWidth + PAD_X} ${svgHeight}`}
          role="img"
          aria-label={t(
            unknownCount > 0
              ? "workspaceDetail.dataflow.pruningTreeAriaWithUnknown"
              : "workspaceDetail.dataflow.pruningTreeAria",
            {
              sink: tree.sink.label ?? "sink",
              vuln: vulnCount,
              safe: safeCount,
              unknown: unknownCount,
            },
          )}
        >
          {layouts.map((layout) => (
            <BranchPath
              key={layout.branch.branch_id ?? layout.rowIdx}
              layout={layout}
              sinkCol={sinkCol}
              sinkX={sinkX}
              sinkY={sinkY}
              sinkLabel={tree.sink.label ?? "sink"}
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
          {hasFold && (
            <FoldedSafeRow
              rowIdx={layouts.length}
              count={safeCount - FOLD_THRESHOLD}
              expanded={foldExpanded}
              highlighted={hoveredBranch != null && foldedIds.includes(hoveredBranch)}
              onToggle={() => setFoldExpanded((cur) => !cur)}
              sinkCol={sinkCol}
              t={t}
            />
          )}
          {sharedArcs.map((arc, i) => (
            <SameLineArcView key={i} arc={arc} />
          ))}
          {/* sink 靶心：有打通枝 → 红脉动圆环；safe-only → 灰虚线圆环（无输入到达） */}
          <SinkTarget
            x={sinkX}
            y={sinkY}
            hasVuln={hasVuln}
            label={tree.sink.label ?? "sink"}
            note={tree.sink.note}
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

/** 树头徽章：sink 名 + file:line + rule_id/class + finding IDs + 迷你比例条。
 *  比例条三段式（2026-08-26 计数口径修复）：红=打通 / 绿=剪断 / 琥珀=未判定——
 *  unknown 枝不再被画进绿色段（旧实现绿段宽 = 100−vulnPct，未判定被视觉等同「安全」）。 */
function TreeHeader({
  tree,
  t,
  vulnCount,
  safeCount,
  unknownCount,
  hasVuln,
}: {
  tree: DataflowTree;
  t: ReturnType<typeof useTranslation>["t"];
  vulnCount: number;
  safeCount: number;
  unknownCount: number;
  hasVuln: boolean;
}) {
  const findingIds = tree.findings.map((f) => f.id).filter(Boolean).join(", ");
  const total = Math.max(1, vulnCount + safeCount + unknownCount);
  const pct = (n: number) => `${(n / total) * 100}%`;
  const hasUnknown = unknownCount > 0;
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
        <span className="text-xs text-muted-foreground" data-minibar-text="">
          {t(
            hasUnknown
              ? "workspaceDetail.dataflow.minibarWithUnknown"
              : "workspaceDetail.dataflow.minibar",
            { vuln: vulnCount, safe: safeCount, unknown: unknownCount },
          )}
        </span>
        <span
          className="inline-flex h-2 w-16 overflow-hidden rounded-full border border-border"
          role="img"
          data-minibar=""
          aria-label={t(
            hasUnknown
              ? "workspaceDetail.dataflow.minibarAriaWithUnknown"
              : "workspaceDetail.dataflow.minibarAria",
            { vuln: vulnCount, safe: safeCount, unknown: unknownCount },
          )}
        >
          <span data-minibar-seg="vuln" className="bg-[hsl(var(--c-red))]" style={{ width: pct(vulnCount) }} />
          <span data-minibar-seg="safe" className="bg-[hsl(var(--c-green))]" style={{ width: pct(safeCount) }} />
          {hasUnknown && (
            <span
              data-minibar-seg="unknown"
              className="bg-[hsl(var(--c-amber))] opacity-80"
              style={{ width: pct(unknownCount) }}
            />
          )}
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
  sinkLabel,
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
  sinkLabel: string;
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

  // 剪断点 step（effective sanitizer 所在节点 step）。匹配加 file 校验——
  // 真实数据不同文件 line 巧合相同会误定位剪断点到错误列。
  const effSan = branch.sanitizers.find((s) => s.effective === true);
  let cutStep = -1;
  if (effSan && verdict === "safe") {
    const idx = branch.nodes.findIndex(
      (n) =>
        n.line != null &&
        effSan.line != null &&
        n.line === effSan.line &&
        (effSan.file == null || n.file == null || n.file === effSan.file),
    );
    cutStep = idx >= 0 ? idx + 1 : branch.nodes.length;
  }

  // 剪断枝只画到剪断点（spec §5：绿实线至防护节点 + 残端，不到 sink）——
  // 剪断点之后的节点不渲染、path 不延伸：否则主 path 先画到枝尾再折回剪断点、
  // 后续节点与残端叠在一起（2026-08-21 真实数据「连线错乱」根因；防护在中途是常态）。
  // 剪断点之后的传播信息不丢失——明细行（BranchRow）仍列全部节点。
  const lastStep = !reachesSink && cutStep > 0 ? cutStep : branch.nodes.length;

  // 主 path 终点：打通枝 → sink 靶心；剪断枝 → 剪断点节点
  const endX = reachesSink ? sinkX : (cutStep > 0 ? xOf(cutStep) : xOf(branch.nodes.length));
  const endY = reachesSink ? sinkY : y;

  // 构造 path d：source → 节点链（至 lastStep）→ 终点（同枝节点同 y；最后一段汇入 sink 用贝塞尔）
  const pts: { x: number; y: number }[] = [{ x: sourceX, y }];
  for (let i = 0; i < lastStep; i++) pts.push({ x: xOf(i + 1), y });
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
      /* 键盘可达（2026-08-26 UX 修复）：枝条可 Tab 聚焦，Enter/Space 与点击同 toggle 选中 */
      tabIndex={branch.branch_id ? 0 : undefined}
      role={branch.branch_id ? "button" : undefined}
      aria-label={
        branch.branch_id
          ? t("workspaceDetail.dataflow.branchAria", {
              source: branch.source.label ?? "source",
              sink: sinkLabel,
              verdict: verdictShort(verdict, t),
            })
          : undefined
      }
      onKeyDown={(e) => {
        if (!branch.branch_id) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(branch.branch_id);
        }
      }}
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
      {/* 节点 + 防护盾 + 公共函数下标（剪断枝只渲染到剪断点 lastStep） */}
      {branch.nodes.slice(0, lastStep).map((node, i) => {
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
 *  副行收进 pill（2026-08-21 重叠修复）：裸画在 pill 底边外 y+15 会与 step-1 节点标签
 *  横向重叠 ~58px 且纵向同带 → 有副行时 pill 高度撑到 34，主/副行全部收进 rect。
 *  2ND 存储中转枝（source.type === "storage"）：副行位置换琥珀色「⟳ 存储中转」标记
 *  （与 meta 互斥——storage 枝 entry 通常为空，meta 全文进 <title>），
 *  tooltip 用 spec §5 白话「经过存储中转：先存进数据库，读出来才发起请求」。
 *  跨树 source 提示（spec §5「跨树 source 提示」）：同一入口（label+entry）出现在多棵树时，
 *  tooltip 注「同一入口还流向：[其它 tree 的 sink 名]」，避免误读为重复数据（与存储提示并存）。
 *  tooltip 走原生 <title>（data-tooltip 的 CSS ::after 在 SVG <g> 上无 containing block，
 *  定位回退到视口容器 → 所有浮层叠到同一处，2026-08-21 已全量移除）。 */
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
  // 副信息行：METHOD /route（storage 的 type 不直译进副行——白话标记行承载）
  const metaParts = [isStorage ? null : source.type, source.entry].filter(Boolean);
  const metaText = metaParts.join(" · ");
  const hasMeta = metaParts.length > 0;
  // 副行与存储标记互斥占同一行位（pill 最多双行）
  const hasSub = hasMeta || isStorage;
  // 主/副文本按列宽预算截断（真实数据 label/entry 长串会溢出到邻列与节点文字重叠）
  const shownLabel = fitLabel(label, COL_W - 28);
  const shownMeta = fitLabel(metaText, COL_W - 20);
  const labelCut = shownLabel !== label;
  const metaCut = shownMeta !== metaText;
  // pill 宽度按主/副两行最宽者撑起（2026-08-26 线穿字修复副因：宽只按主行算时，
  // 长 entry 副行溢出 rect 右缘，主 path 从 pill 中心穿出后正好压在露出的 meta 尾巴上）
  const subW = isStorage ? 80 : textWidthPx(shownMeta);
  const w = Math.min(
    COL_W - 8,
    Math.max(56, textWidthPx(shownLabel) + 16, hasSub ? subW + 16 : 0),
  );
  const h = hasSub ? PILL_HALF_H_MAX * 2 : 22;
  const topY = -h / 2;
  // 主行基线：有副行时上移，无副行居中
  const mainY = hasSub ? -4 : 4;
  const subY = 11;
  // tooltip：叙事原句（note，label 归一为短标识符后全文在此；无 note 才退截断全名）
  // + 存储中转白话（2ND 枝）+ 跨树提示（并存拼接）；都无 → source 基本描述
  const tipParts: string[] = [];
  if (source.note) tipParts.push(source.note);
  else if (labelCut) tipParts.push(label);
  if (metaCut && metaText) tipParts.push(metaText);
  if (isStorage) tipParts.push(t("workspaceDetail.dataflow.storageRelayFull"));
  if (crossTreeTip) tipParts.push(t("workspaceDetail.dataflow.crossTreeTooltip", { sinks: crossTreeTip }));
  const tooltip =
    tipParts.length > 0
      ? tipParts.join(" ｜ ")
      : [source.label, source.type, source.entry].filter(Boolean).join(" · ") || "source";
  return (
    <g data-source="" data-node="0" x={x} transform={`translate(${x} ${y})`} className="source-pill">
      <title>{tooltip}</title>
      {/* 不透明底（2026-08-26 线穿字修复）：主 path 起点是 pill 正中心 (0,0)，首段横穿
          pill 文字带；16% 半透明面盖不住线 → 红虚线直接压在 source 文字上，每枝一条、
          满屏线字互压（NodeGoat 15 树真实数据「乱糟糟」根因）。底 rect 用卡面实色
          （--card，与树卡底同色视觉无缝），先画在半透明面之下、二者都在枝条 path 之上 →
          穿 pill 的线段被盖住，线视觉上从 pill 右缘接出。 */}
      <rect x={-6} y={topY} width={w} height={h} rx={12} className="source-pill-bg" data-pill-bg="" />
      <rect x={-6} y={topY} width={w} height={h} rx={12} className="source-pill" />
      <text x={2} y={mainY} className="source-pill-txt" textAnchor="start" data-source-label="">
        {shownLabel}
      </text>
      {/* 副信息行：type · METHOD /route（spec §5 source 行要求，收进 pill 内）。
          与存储标记互斥占同一行位（storage 时 entry 全文进 <title>）。 */}
      {hasMeta && !isStorage && (
        <text x={2} y={subY} className="source-meta-txt" textAnchor="start" data-source-meta="">
          {shownMeta}
        </text>
      )}
      {/* 存储中转白话标记（2ND 枝 source.type=storage）：琥珀色，与 meta 互斥占副行位 */}
      {isStorage && (
        <text x={2} y={subY} className="storage-relay-txt" textAnchor="start" data-storage-relay="">
          {t("workspaceDetail.dataflow.storageRelayMark")}
        </text>
      )}
    </g>
  );
}

/** 节点：圆 + 函数名（两行）+ 防护盾（黄=绕过 / 绿=有效=剪断点）+ 剪刀（剪断点）。
 *  data-node = step_index（1-based 中间节点；source 是 step 0 在 SourcePill）。
 *  函数名两行（2026-08-21 重叠修复）：真实数据 func 是 40-70 字符自然语言描述，
 *  单行一刀切到 ~26 半角信息量暴跌；两行各按列宽预算截断（相邻列不叠），line 号拼尾行。
 *  公共函数下标（spec §5「公共函数 ⟳ N 枝经过」）：func 经多枝共用时，
 *  节点下方加「⟳ 公共函数 · N 枝经过」（N=树内 func 重名计数，前端自算），
 *  hover <title> 说明剪断了哪几条枝。 */
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
  // 标签 = func:line，按列宽预算拆 ≤2 行（预算 = COL_W-24 严格列内），全名进 <title>
  const fullLabel = node.line != null ? `${node.func ?? "?"}:${node.line}` : (node.func ?? "?");
  const lines = fitLabelTwoLines(fullLabel, COL_W - 24);
  const labelCut = lines.join("").replace("…", "") !== fullLabel;
  // tooltip：note=LLM 叙事原句（label 归一为短标识符后全文在此）优先，
  // 与 pubFunc 提示/截断全名并存（2026-08-27 label 归一配套）
  const nodeTooltip =
    [node.note, pubTooltip ?? (labelCut ? fullLabel : null)].filter(Boolean).join(" ｜ ") ||
    undefined;
  return (
    <g data-node={step} x={x} transform={`translate(${x} ${y})`}>
      {nodeTooltip && <title>{nodeTooltip}</title>}
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
      {/* 函数名（≤2 行，行距 11；首行基线 NODE_LABEL_Y1） */}
      <text
        x={0}
        y={NODE_LABEL_Y1}
        className="fill-[hsl(var(--foreground))]"
        fontSize={10}
        textAnchor="middle"
        data-node-label=""
      >
        {lines.map((l, i) => (
          <tspan key={i} x={0} dy={i === 0 ? 0 : 11}>
            {l}
          </tspan>
        ))}
      </text>
      {/* 公共函数下标（spec §5）：⟳ 公共函数 · N 枝经过 */}
      {isPubFunc && (
        <text
          x={0}
          y={NODE_LABEL_Y1 + 22}
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

/** 折叠剪断枝行：「+N 条枝被剪断」⇄「收起 N 条剪断枝」（点击切换）。
 *  联动（2026-08-26 折叠枝联动修复）：hover 被折叠枝的明细行 → 本行高亮
 *  （图中反馈「该枝在折叠批次里」，不再无响应像坏了）；点击展开全部剪断枝。 */
function FoldedSafeRow({
  rowIdx,
  count,
  expanded,
  highlighted,
  onToggle,
  sinkCol,
  t,
}: {
  rowIdx: number;
  count: number;
  expanded: boolean;
  highlighted: boolean;
  onToggle: () => void;
  sinkCol: number;
  t: ReturnType<typeof useTranslation>["t"];
}) {
  const y = yOf(rowIdx);
  const x1 = xOf(0);
  const x2 = xOf(sinkCol) - COL_W * 0.35;
  return (
    <g
      data-collapsed-safe=""
      data-hovered={highlighted ? "" : undefined}
      className="folded-safe-row"
      role="button"
      tabIndex={0}
      aria-label={t("workspaceDetail.dataflow.foldedSafeExpandHint")}
      onClick={onToggle}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onToggle();
        }
      }}
    >
      <title>{t("workspaceDetail.dataflow.foldedSafeExpandHint")}</title>
      <path d={hop(x1, y, x2, y)} className="folded-safe" />
      <text x={(x1 + x2) / 2} y={y - 6} className="fill-[hsl(var(--c-green))]" fontSize={11} textAnchor="middle">
        {expanded
          ? t("workspaceDetail.dataflow.foldedSafeCollapse", { count })
          : t("workspaceDetail.dataflow.foldedSafe", { count })}
      </text>
    </g>
  );
}

/** 同一函数青色点线弧（spec §5「同一函数虚线 ⟳」：不合并节点）。
 *  与「公共函数 ⟳ N 枝经过」节点下标是 spec 表格两行独立元素：弧=跨枝同一性（点线连同名节点），
 *  下标=N 枝经过计数（在 NodeView 节点下方）。
 *  每弧不带文字标注（2026-08-21 重叠修复）：多共享函数时各弧 midX/midY 相近，
 *  「⟳ 同一函数」小标互叠；弧语义收进 LegendBar 图例一项。 */
function SameLineArcView({ arc }: { arc: SameLineArc }) {
  const midX = (arc.from.x + arc.to.x) / 2;
  const midY = Math.min(arc.from.y, arc.to.y) - 24;
  const d = `M ${arc.from.x} ${arc.from.y} Q ${midX} ${midY}, ${arc.to.x} ${arc.to.y}`;
  return (
    <g data-sameline="" className="sameline">
      <path d={d} className="sameline" />
    </g>
  );
}

/** sink 靶心：有打通枝 → 红实线圆环 + 脉动；safe-only → 灰虚线圆环。
 *  灰靶心带「无输入到达」标注（spec §5 白话表：sink 无枝到达 = 无输入到达，禁「未被触及」）。
 *  sink 名两行（2026-08-21：真实 sink 名 40+ 字符，单行截断信息量低），全名进 <title>。 */
function SinkTarget({
  x,
  y,
  hasVuln,
  label,
  note,
  t,
}: {
  x: number;
  y: number;
  hasVuln: boolean;
  label: string;
  /** LLM 自立树叙事原句（label 归一为短标识符后全文在此）。 */
  note?: string | null;
  t: ReturnType<typeof useTranslation>["t"];
}) {
  const sinkCls = hasVuln ? "sink-pulse" : "sink-idle";
  const noInputTip = hasVuln ? undefined : t("workspaceDetail.dataflow.sinkNoInput");
  // sink 名按列宽预算拆 ≤2 行（长名溢出 viewBox 右界被裁=「文字缺失」），全名进 <title>
  const lines = fitLabelTwoLines(label, COL_W - 24);
  // tooltip：叙事原句（note）优先，否则 label 全名（截断与否都给全名，对齐旧语义）
  const tooltip = [note ?? label, noInputTip].filter(Boolean).join(" · ");
  return (
    <g data-sink-target={hasVuln ? "vuln" : "safe"} transform={`translate(${x} ${y})`} className={sinkCls}>
      {/* 原生 SVG tooltip（hover 教读图；截断时含全名） */}
      {tooltip && <title>{tooltip}</title>}
      <circle r={16} className={sinkCls} />
      <circle r={6} fill={hasVuln ? "hsl(var(--c-red))" : "hsl(var(--muted-foreground))"} opacity={hasVuln ? 0.8 : 0.4} />
      <text x={0} y={30} className="sink-label" textAnchor="middle" data-sink-label="">
        {lines.map((l, i) => (
          <tspan key={i} x={0} dy={i === 0 ? 0 : 12}>
            {l}
          </tspan>
        ))}
      </text>
      {!hasVuln && (
        <text x={0} y={54} className="sink-noinput-txt" textAnchor="middle" data-sink-noinput="">
          {noInputTip}
        </text>
      )}
    </g>
  );
}

/** 缩放平移容器（2026-08-21 交互重做；2026-08-26 UX 第二批修复）：
 *  - 滚轮：无修饰键放行（页面自然滚动，不再劫持）；Ctrl/⌘+wheel 缩放（接管浏览器页缩放）。
 *    原生 addEventListener({passive:false}) 注册——React 合成 onWheel 在 root 上是 passive，
 *    preventDefault 无效。
 *  - 缩放：直接放大 svg 的 width 属性（viewBox 不变）——SVG 语义放大，布局尺寸随之
 *    变化 → overflow:auto 滚动条自动正确。替代旧 CSS transform:scale（不改布局尺寸，
 *    scale>1 时溢出被裁、滚动条到不了右侧内容）。
 *  - 缩放锚定（2026-08-26）：以光标为锚补偿 scrollLeft/scrollTop（按钮缩放锚视口中心）——
 *    不补偿时光标下的点漂移，用户要反复拖回。
 *  - 拖拽平移：驱动 scrollLeft/scrollTop（程序化滚动，滚动条同步）——替代旧 translate
 *    双轨（translate 把内容移出滚动条可达范围 = 图「错乱/丢失」）。mousedown
 *    preventDefault + 容器 userSelect:none（2026-08-26）：拖图不再把 SVG 文字选蓝。
 *  - 控件：− / 百分比(reset) / + 三个按钮，挂滚动容器外的 relative wrapper 上
 *    （2026-08-26）：absolute 在 overflow:auto 容器内会随内容滚出视口——深树横向滚动
 *    后按钮消失，想缩放得先滚回左上角。 */
function ZoomViewport({
  children,
  maxHeight,
}: {
  children: React.ReactElement<{ width?: number | string }>;
  maxHeight: number;
}) {
  const { t } = useTranslation();
  const [scale, setScale] = useState(1);
  const ref = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ x: number; y: number; sl: number; st: number } | null>(null);
  // 缩放锚点：zoom 发起时记录（光标/视口中心相对容器偏移 + 当时 scroll），scale 生效后补偿
  const anchorRef = useRef<{ ox: number; oy: number; sl: number; st: number; from: number } | null>(null);

  const zoomAt = useCallback((factor: number, ox: number, oy: number) => {
    const el = ref.current;
    if (!el) return;
    setScale((s) => {
      const next = Math.min(3, Math.max(0.3, s * factor));
      if (next !== s) anchorRef.current = { ox, oy, sl: el.scrollLeft, st: el.scrollTop, from: s };
      return next;
    });
  }, []);

  // 按钮缩放锚视口中心（无光标语义）
  const zoomFromCenter = useCallback(
    (factor: number) => {
      const el = ref.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      zoomAt(factor, r.width / 2, r.height / 2);
    },
    [zoomAt],
  );

  const resetZoom = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setScale((s) => {
      if (s !== 1) anchorRef.current = { ox: r.width / 2, oy: r.height / 2, sl: el.scrollLeft, st: el.scrollTop, from: s };
      return 1;
    });
  }, []);

  useEffect(() => {
    const el = ref.current;
    const a = anchorRef.current;
    if (!el || !a) return;
    anchorRef.current = null;
    // svg width 已按新 scale 渲染后再补滚动（同帧布局完成，scrollLeft 不被 clamp 回）
    el.scrollLeft = nextScrollForZoom(a.sl, a.ox, a.from, scale);
    el.scrollTop = nextScrollForZoom(a.st, a.oy, a.from, scale);
  }, [scale]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return; // 纯滚轮 → 页面/容器自然滚动
      e.preventDefault(); // Ctrl+wheel 浏览器默认是页缩放 → 接管为图缩放（光标锚定）
      const r = el.getBoundingClientRect();
      zoomAt(e.deltaY > 0 ? 0.9 : 1.1, e.clientX - r.left, e.clientY - r.top);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    const el = ref.current;
    if (!el) return;
    // 阻止拖拽进入文本选择（SVG text 默认可选，拖图会把满屏文字选蓝）
    e.preventDefault();
    dragRef.current = { x: e.clientX, y: e.clientY, sl: el.scrollLeft, st: el.scrollTop };
  }, []);

  const onMouseMove = useCallback((e: React.MouseEvent) => {
    const d = dragRef.current;
    const el = ref.current;
    if (!d || !el) return;
    el.scrollLeft = d.sl - (e.clientX - d.x);
    el.scrollTop = d.st - (e.clientY - d.y);
  }, []);

  const endDrag = useCallback(() => {
    dragRef.current = null;
  }, []);

  // svg width（数字 prop）× scale：viewBox 不变 → 语义放大，布局尺寸联动滚动条
  const baseW = typeof children.props.width === "number" ? children.props.width : null;
  const scaled = baseW != null ? cloneElement(children, { width: baseW * scale }) : children;
  const btnCls =
    "rounded border border-border bg-card px-1.5 text-xs text-muted-foreground hover:text-primary";

  return (
    <div className="relative" data-zoom-wrap="">
      <div
        ref={ref}
        data-viewport=""
        data-max-height={String(maxHeight)}
        style={{ maxHeight, overflow: "auto", cursor: "grab", userSelect: "none" }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={endDrag}
        onMouseLeave={endDrag}
      >
        {scaled}
      </div>
      <span className="absolute right-2 top-2 z-10 flex items-center gap-1">
        <button
          type="button"
          data-zoom-out=""
          onClick={() => zoomFromCenter(0.9)}
          className={btnCls}
          aria-label={t("workspaceDetail.dataflow.zoomOut")}
        >
          −
        </button>
        <button
          type="button"
          onClick={resetZoom}
          data-zoom-reset=""
          className={btnCls}
          title={t("workspaceDetail.dataflow.zoomReset")}
        >
          {Math.round(scale * 100)}%
        </button>
        <button
          type="button"
          data-zoom-in=""
          onClick={() => zoomFromCenter(1.1)}
          className={btnCls}
          aria-label={t("workspaceDetail.dataflow.zoomIn")}
        >
          +
        </button>
      </span>
    </div>
  );
}
