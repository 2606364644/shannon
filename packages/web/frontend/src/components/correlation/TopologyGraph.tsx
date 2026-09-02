import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { CorrelationDetail } from "@/api/types";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { anchorPair, type Box } from "@/lib/topology-anchors";

/**
 * 服务拓扑图（D5，spec 2026-08-24）：纯 SVG——按调用层级分层（入口第 0 层，
 * layer(to) ≥ layer(from)+1 长路径松弛），层间左→右、层内垂直均分；
 * 节点 = 圆角矩形 + 服务名 + role 徽标文字；边 = 直线箭头 + 中点 protocol 标签 +
 * status 语义着色（ok=green / low=amber / unverified=muted / error=red /
 * declared-missing=muted 虚线）。点边 → 下方展开该边 calls 表。
 *
 * layout() 是纯函数（导出便于测试）：同输入同输出，无 React 依赖。
 */

export type CorrTopology = NonNullable<CorrelationDetail["topology"]>;
export type CorrEdge = CorrTopology["edges"][number];

export interface NodePos {
  name: string;
  x: number;
  y: number;
  role: string;
}

/** 节点盒尺寸（rect 宽高，中心对齐 NodePos.x/y）。 */
const NODE_W = 150;
const NODE_H = 56;

/**
 * 布局纯函数（调用层级分层）：入口第 0 层；沿边松弛 layer(to) ≥ layer(from)+1
 * （迭代至稳定，容忍环）；无前驱的非入口服务落第 0 层（与入口并列——它们不依赖
 * 任何人）。层间从左到右均分画布宽，层内垂直均分；单层居中。高度 =
 * max(各层节点数, 1) × heightPerNode + 40（空服务不塌缩，留呼吸边距）。
 *
 * 原「入口左列 / 其余右列」两列布局：backend 间调用边在右列内部变成垂直线、
 * protocol 标签叠在节点身上；分层后 backend 按被调深度各归其列，跨层边走水平带。
 */
export function layout(
  services: { name: string; role: string }[],
  edges: { from: string; to: string }[] = [],
  width = 560,
  heightPerNode = 90,
): { nodes: NodePos[]; height: number } {
  // 分层：入口初始化为 0，沿边反复抬高被调方（最多 |services| 轮，环收敛）
  const layer = new Map<string, number>();
  services.forEach((s) => { if (s.role === "entrypoint") layer.set(s.name, 0); });
  const known = new Set(services.map((s) => s.name));
  for (let round = 0; round < services.length; round++) {
    let changed = false;
    for (const e of edges) {
      if (!known.has(e.from) || !known.has(e.to)) continue;
      const lf = layer.get(e.from);
      if (lf === undefined) continue;
      if ((layer.get(e.to) ?? -1) < lf + 1) { layer.set(e.to, lf + 1); changed = true; }
    }
    if (!changed) break;
  }
  // 无前驱的非入口（含孤立）服务落第 0 层，与入口并列
  services.forEach((s) => { if (!layer.has(s.name)) layer.set(s.name, 0); });

  const maxLayer = Math.max(0, ...layer.values());
  const columns: { name: string; role: string }[][] = Array.from({ length: maxLayer + 1 }, () => []);
  services.forEach((s) => columns[layer.get(s.name)!].push(s));
  const height = Math.max(...columns.map((c) => c.length), 1) * heightPerNode + 40;

  const nodes: NodePos[] = [];
  const span = width - 40; // 左右各 20 边距
  columns.forEach((column, li) => {
    const x = maxLayer === 0 ? width / 2 : 20 + (span / (maxLayer + 1)) * (li + 0.5);
    column.forEach((s, i) => nodes.push({ name: s.name, role: s.role, x, y: 40 + i * heightPerNode + 20 }));
  });
  return { nodes, height };
}

/** 边 status → 语义色 token class（repo 既有语义色：green/amber/red/muted-foreground）。 */
export function statusClass(status: string): string {
  switch (status) {
    case "ok":
      return "text-green";
    case "low":
      return "text-amber";
    case "error":
      return "text-red";
    default:
      // unverified / declared-missing / 未知值：低调灰
      return "text-muted-foreground";
  }
}

/** role → 徽标文案（复用表单侧 roleEntrypoint/roleBackend 键；未知 role 原样透传）。 */
function roleLabel(role: string, t: (k: string) => string): string {
  if (role === "entrypoint") return t("scan.correlation.roleEntrypoint");
  if (role === "backend") return t("scan.correlation.roleBackend");
  return role;
}

export function TopologyGraph({ topology }: { topology: CorrTopology }) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<number | null>(null);
  const { nodes, height } = layout(topology.services, topology.edges);
  const byName = new Map(nodes.map((n) => [n.name, n]));
  const width = 560;

  return (
    /* max-w 锚定 viewBox 宽（~1:1 渲染）：不限宽时整图随容器放大 2-3×，节点文字失衡 */
    <div className="max-w-[600px]">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        role="img"
        aria-label={t("scan.correlation.topologyTitle")}
      >
        <defs>
          <marker
            id="corr-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 1 L 9 5 L 0 9 z" fill="hsl(var(--muted-foreground))" />
          </marker>
        </defs>
        {/* 边先画（在节点下层）：anchorPair 按节点相对位置选面向侧（分层布局下
            跨层边走水平带；同层/环边自动转垂直上下缘，不再垂直叠在节点身上） */}
        {topology.edges.map((e, i) => {
          const from = byName.get(e.from);
          const to = byName.get(e.to);
          if (!from || !to) return null;
          const boxOf = (n: NodePos): Box =>
            ({ x: n.x - NODE_W / 2, y: n.y - NODE_H / 2, w: NODE_W, h: NODE_H });
          const { from: p1, to: p2 } = anchorPair(boxOf(from), boxOf(to));
          const x1 = p1.x;
          const y1 = p1.y;
          const x2 = p2.x;
          const y2 = p2.y;
          const mx = (x1 + x2) / 2;
          const my = (y1 + y2) / 2;
          const isSel = selected === i;
          const dashed = e.status === "declared-missing";
          return (
            <g
              key={`${e.from}-${e.to}-${i}`}
              data-testid={`topo-edge-${e.from}-${e.to}`}
              className={`${statusClass(e.status)} cursor-pointer`}
              onClick={() => setSelected(isSel ? null : i)}
            >
              <title>{`${e.from} → ${e.to} · ${e.protocol} · ${e.status}`}</title>
              {/* 加粗透明命中区：细线难点中 */}
              <path d={`M ${x1} ${y1} L ${x2} ${y2}`} stroke="transparent" strokeWidth={14} fill="none" />
              <path
                d={`M ${x1} ${y1} L ${x2} ${y2}`}
                className="stroke-current"
                strokeWidth={isSel ? 3 : 2}
                strokeDasharray={dashed ? "6 4" : undefined}
                markerEnd="url(#corr-arrow)"
                fill="none"
              />
              <text
                x={mx}
                y={my - 6}
                textAnchor="middle"
                fontSize={11}
                className={`fill-current ${isSel ? "font-medium" : ""}`}
                paintOrder="stroke"
                stroke="hsl(var(--background))"
                strokeWidth={3}
              >
                {e.protocol}
              </text>
            </g>
          );
        })}
        {/* 节点：圆角矩形 + 服务名 + role 徽标文字 */}
        {nodes.map((n) => (
          <g key={n.name} data-testid={`topo-node-${n.name}`}>
            <rect
              x={n.x - NODE_W / 2}
              y={n.y - NODE_H / 2}
              width={NODE_W}
              height={NODE_H}
              rx={10}
              fill="hsl(var(--card))"
              stroke="hsl(var(--border))"
            />
            <text x={n.x} y={n.y - 4} textAnchor="middle" fontSize={13} fill="hsl(var(--foreground))">
              {n.name}
            </text>
            <text x={n.x} y={n.y + 14} textAnchor="middle" fontSize={10} className="fill-current text-muted-foreground">
              {roleLabel(n.role, t)}
            </text>
          </g>
        ))}
      </svg>
      <p className="mt-1 text-xs text-muted-foreground">{t("scan.correlation.clickEdgeHint")}</p>
      {/* 点边展开：该边 calls 表（method / file:line / snippet / confidence / evidence） */}
      {selected !== null && topology.edges[selected] && (
        <div className="mt-3" data-testid="topo-calls">
          <div className="mb-1 font-mono text-xs text-muted-foreground">
            {topology.edges[selected].from} → {topology.edges[selected].to} ·{" "}
            {t("scan.correlation.edgeCalls")}
          </div>
          {topology.edges[selected].calls.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("scan.correlation.noCalls")}</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("scan.correlation.colMethod")}</TableHead>
                  <TableHead>{t("scan.correlation.colCallSite")}</TableHead>
                  <TableHead>{t("scan.correlation.colSnippet")}</TableHead>
                  <TableHead>{t("scan.correlation.colConfidence")}</TableHead>
                  <TableHead>{t("scan.correlation.colEvidence")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {topology.edges[selected].calls.map((c, ci) => (
                  <TableRow key={ci}>
                    <TableCell className="font-mono text-xs">{c.method}</TableCell>
                    <TableCell className="font-mono text-xs">
                      {c.call_site.file}:{c.call_site.line}
                    </TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {c.call_site.snippet}
                    </TableCell>
                    <TableCell className="text-xs">{c.confidence}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{c.evidence}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      )}
    </div>
  );
}
