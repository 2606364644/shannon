import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Info, Redo2, Undo2, LayoutGrid, Maximize, Minus, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  addTopologyEdge, deleteTopologyEdge, moveTopologyNode, redoTopology, undoTopology,
  resetTopologyLayout, restoreTopologyAiEdge, setTopologyEdgeEnabled, updateTopologyEdge,
  validateTopologyDraft, type TopologyDraftState,
} from "@/lib/correlation-topology-draft";
import { anchorPair, type Box } from "@/lib/topology-anchors";
import { TopologyTables } from "./TopologyTables";

interface Props {
  state: TopologyDraftState;
  onState: (state: TopologyDraftState) => void;
  scans?: unknown[];
  availableRepos?: string[];
  onAddNode?: (repo: string) => void;
  onRemoveNode?: (repo: string) => void;
}

/** 节点盒尺寸（viewBox 坐标）——lib 侧 clamp 与此处共用同一口径。 */
const NODE_W = 105;
const NODE_H = 48;
/** 画布 viewBox（与 svg viewBox 硬编码一致）。 */
const CANVAS_W = 800;
const CANVAS_H = 600;
/** 拖动时节点离画布边缘的最小留白。 */
const EDGE_MARGIN = 6;
/** repo 名截断阈值：11px mono 下 105px 盒内可容 ~14 字符，超出截断 + title 悬停看全名。 */
const NAME_MAX = 14;

/* ===== 画布 pan/zoom（视口态，非内容态——不进 undo history）=====
 * 跨仓微服务动辄 6+ 服务，固定 800×600 视口装不下：滚轮缩放（指针锚定）+
 * 空白拖动平移 + 右下角缩放条（−/100%/＋/fit）。世界坐标 = 节点坐标（不变），
 * 视口坐标 = viewBox 原坐标，映射：world = (viewport - t) / k。 */
export interface ViewTransform { x: number; y: number; k: number }
/** 缩放范围：0.3（全景，服务 15+ 时仍可辨）～3（看文件级证据细节）。 */
const MIN_K = 0.3;
const MAX_K = 3;
const ZOOM_STEP = 1.2;

function clampK(k: number): number {
  return Math.min(MAX_K, Math.max(MIN_K, k));
}

/**
 * 客户端坐标 → viewBox 视口坐标。preserveAspectRatio 默认 meet 下容器宽高比
 * ≠ 4:3 时内容居中等比（宽容器左右留白）：rect.width 直接按比例换算有漂移，
 * 须按实际渲染 scale + 居中偏移换算（缩放后漂移被 k 放大，指针锚定会跑偏）。
 */
export function viewportPointOf(
  svg: SVGSVGElement | null, clientX: number, clientY: number,
): { x: number; y: number } {
  const rect = svg?.getBoundingClientRect();
  if (!rect) return { x: 0, y: 0 };
  const scale = Math.min(rect.width / CANVAS_W, rect.height / CANVAS_H) || 1;
  const offsetX = (rect.width - CANVAS_W * scale) / 2;
  const offsetY = (rect.height - CANVAS_H * scale) / 2;
  return { x: (clientX - rect.left - offsetX) / scale, y: (clientY - rect.top - offsetY) / scale };
}

/** 以视口坐标 vp 为锚缩放 factor 倍（指针下的世界点不动——内容朝指针收放）。 */
export function zoomAtPoint(
  v: ViewTransform, vp: { x: number; y: number }, factor: number,
): ViewTransform {
  const k = clampK(v.k * factor);
  const applied = k / v.k;
  return { k, x: vp.x - (vp.x - v.x) * applied, y: vp.y - (vp.y - v.y) * applied };
}

/** 节点包围盒适配视口：只缩小不放大（k ≤ 1——小图维持 100% 细节，不糊不迫近）。 */
export function fitTransform(
  nodes: { position: { x: number; y: number } }[], pad = 40,
): ViewTransform {
  if (!nodes.length) return { x: 0, y: 0, k: 1 };
  const minX = Math.min(...nodes.map((n) => n.position.x));
  const minY = Math.min(...nodes.map((n) => n.position.y));
  const maxX = Math.max(...nodes.map((n) => n.position.x + NODE_W));
  const maxY = Math.max(...nodes.map((n) => n.position.y + NODE_H));
  const k = Math.min(1, clampK(Math.min((CANVAS_W - pad * 2) / (maxX - minX), (CANVAS_H - pad * 2) / (maxY - minY))));
  return {
    k,
    x: (CANVAS_W - (maxX - minX) * k) / 2 - minX * k,
    y: (CANVAS_H - (maxY - minY) * k) / 2 - minY * k,
  };
}

export function TopologyEditor({ state, onState, scans = [], availableRepos, onAddNode, onRemoveNode }: Props) {
  const { t } = useTranslation();
  const svgRef = useRef<SVGSVGElement>(null);
  const dragRepo = useRef<string | null>(null);
  // 连线态 state 化（原 ref 不触发渲染，拖线全程零视觉反馈）。connectPos = 预览线终点
  // （鼠标的世界坐标）。成边通道 = 在目标节点上松手；空白/画布外松手与 Esc = 取消。
  const [connectFrom, setConnectFrom] = useState<string | null>(null);
  const [connectPos, setConnectPos] = useState<{ x: number; y: number } | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  // 画布视口（pan/zoom）：panning 态只驱动 grab→grabbing 光标；origin 快照让平移
  // 增量从落点起算（不受中间 setView 重渲染影响）。
  const [view, setView] = useState<ViewTransform>({ x: 0, y: 0, k: 1 });
  const [panning, setPanning] = useState(false);
  const panRef = useRef<{ vx: number; vy: number; origin: ViewTransform } | null>(null);
  const selectedEdge = state.draft.edges.find((edge) => edge.id === selectedEdgeId) ?? null;
  const liveIdentities = new Set(state.draft.edges.map((edge) => `${edge.from}\n${edge.to}\n${edge.protocol}`));
  const removedAiEdges = (state.analysis?.result?.edges ?? []).filter(
    (edge) => !liveIdentities.has(`${edge.from}\n${edge.to}\n${edge.protocol}`));
  const nodeByRepo = new Map(state.draft.nodes.map((node) => [node.repo, node]));

  // Esc 取消连线（连线态才挂全局监听）
  useEffect(() => {
    if (!connectFrom) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setConnectFrom(null); setConnectPos(null); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [connectFrom]);

  // 滚轮缩放须 native listener + passive:false：React onWheel 挂在 root 的
  // passive listener 上，preventDefault 无效（console 报警），页面会跟着滚。
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      // deltaMode 行/像素两种粒度归一；指数步进让触控板小增量平滑、滚轮格挡有感。
      const unit = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? 33 : 1;
      const factor = Math.exp(-event.deltaY * unit * 0.0016);
      setView((v) => zoomAtPoint(v, viewportPointOf(svg, event.clientX, event.clientY), factor));
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, []);

  /** 指针事件的世界坐标（逆视口变换，预览线跟随鼠标用，不 clamp）。 */
  const rawPoint = (event: React.PointerEvent) => {
    const vp = viewportPointOf(svgRef.current, event.clientX, event.clientY);
    return { x: (vp.x - view.x) / view.k, y: (vp.y - view.y) / view.k };
  };
  /** 节点拖动坐标：clamp 进画布（节点不再能拖出边界「消失」）。 */
  const nodePoint = (event: React.PointerEvent) => {
    const p = rawPoint(event);
    return {
      x: Math.min(Math.max(p.x, EDGE_MARGIN), CANVAS_W - NODE_W - EDGE_MARGIN),
      y: Math.min(Math.max(p.y, EDGE_MARGIN), CANVAS_H - NODE_H - EDGE_MARGIN),
    };
  };

  const boxOf = (node: { position: { x: number; y: number } }): Box =>
    ({ x: node.position.x, y: node.position.y, w: NODE_W, h: NODE_H });
  const connectFromNode = connectFrom ? nodeByRepo.get(connectFrom) : undefined;

  const cancelConnect = () => { setConnectFrom(null); setConnectPos(null); };

  /** 节点包围盒（含盒体）是否溢出当前视口——溢出时 fit 按钮亮圆点作「找回全局」提示。 */
  const needsFit = (() => {
    if (!state.draft.nodes.length) return false;
    const xs = state.draft.nodes.flatMap((n) =>
      [n.position.x * view.k + view.x, (n.position.x + NODE_W) * view.k + view.x]);
    const ys = state.draft.nodes.flatMap((n) =>
      [n.position.y * view.k + view.y, (n.position.y + NODE_H) * view.k + view.y]);
    return Math.min(...xs) < -1 || Math.max(...xs) > CANVAS_W + 1
      || Math.min(...ys) < -1 || Math.max(...ys) > CANVAS_H + 1;
  })();

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
        <span className="ml-auto flex items-center gap-1 text-[11px] text-muted-foreground">
          <Info className="h-3.5 w-3.5 flex-shrink-0" aria-hidden />
          {t("scan.correlation.topology.canvasHint")}
        </span>
      </div>
      <div className="grid gap-3 xl:grid-cols-[minmax(0,2fr)_minmax(18rem,1fr)]">
        <div className="relative">
        <svg ref={svgRef} viewBox={`0 0 ${CANVAS_W} ${CANVAS_H}`} data-testid="topology-canvas"
          className={`h-[420px] w-full touch-none rounded-lg border border-border bg-card ${panning ? "cursor-grabbing" : "cursor-grab"}`}
          onPointerDown={(event) => {
            // 三种手势按落点分流：节点（data-node）拖节点、边（line）选边、手柄随节点——
            // 其余落点（背景/点阵）开画布平移。
            if ((event.target as Element).closest("[data-node], line")) return;
            const vp = viewportPointOf(svgRef.current, event.clientX, event.clientY);
            panRef.current = { vx: vp.x, vy: vp.y, origin: view };
            setPanning(true);
          }}
          onPointerMove={(event) => {
            if (connectFrom) { setConnectPos(rawPoint(event)); return; }
            if (dragRepo.current) {
              const p = nodePoint(event);
              onState(moveTopologyNode(state, dragRepo.current, p.x, p.y));
              return;
            }
            const pan = panRef.current;
            if (pan) {
              const vp = viewportPointOf(svgRef.current, event.clientX, event.clientY);
              setView({ ...pan.origin, x: pan.origin.x + (vp.x - pan.vx), y: pan.origin.y + (vp.y - pan.vy) });
            }
          }}
          onPointerUp={() => { dragRepo.current = null; cancelConnect(); panRef.current = null; setPanning(false); }}
          onPointerLeave={() => { dragRepo.current = null; cancelConnect(); panRef.current = null; setPanning(false); }}>
          <defs>
            <marker id="topology-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
              <path d="M0,0 L8,4 L0,8 z" className="fill-muted-foreground" />
            </marker>
            {/* 画布点阵：自由拖放工作台语义（对齐设计工具画布语言），token 派生全主题适配。
                alpha 0.32 为五主题抽查定值：暗色/亮暗色底（charcoal/gruvbox）0.2x 档不可见。 */}
            <pattern id="topology-dots" width="26" height="26" patternUnits="userSpaceOnUse">
              <circle cx="1.2" cy="1.2" r="1.2" fill="hsl(var(--muted-foreground) / 0.32)" />
            </pattern>
          </defs>
          {/* 视口组：世界坐标内容（节点位置不变）整体 translate+scale。
              点阵铺到远超 800×600 的世界范围——平移/缩小后露出的画布区仍是点阵。 */}
          <g data-testid="topology-viewport" transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
          <rect x={-1600} y={-1200} width={CANVAS_W + 3200} height={CANVAS_H + 2400}
            fill="transparent" data-testid="topology-canvas-bg" />
          <rect x={-1600} y={-1200} width={CANVAS_W + 3200} height={CANVAS_H + 2400}
            fill="url(#topology-dots)" aria-hidden className="pointer-events-none" />
          {state.draft.edges.map((edge) => {
            const from = nodeByRepo.get(edge.from); const to = nodeByRepo.get(edge.to);
            if (!from || !to) return null;
            // 端点按节点相对位置选面向侧（anchorPair）：自由拖放后连线不再横穿节点本体
            const anchors = anchorPair(boxOf(from), boxOf(to));
            return <line key={edge.id} data-testid={`topology-edge-${edge.id.replace(/[^A-Za-z0-9_-]/g, "_")}`}
              x1={anchors.from.x} y1={anchors.from.y} x2={anchors.to.x} y2={anchors.to.y}
              className={`cursor-pointer ${edge.enabled ? (selectedEdgeId === edge.id ? "stroke-primary" : "stroke-muted-foreground") : "stroke-border"}`}
              strokeWidth={selectedEdgeId === edge.id ? 2.5 : 1.5} strokeDasharray={edge.enabled ? undefined : "4 4"}
              markerEnd="url(#topology-arrow)" tabIndex={0} role="button" aria-label={`${edge.from} ${edge.protocol} ${edge.to}`}
              onPointerDown={() => setSelectedEdgeId(edge.id)} onClick={() => setSelectedEdgeId(edge.id)}
              onKeyDown={(e) => e.key === "Enter" && setSelectedEdgeId(edge.id)} />;
          })}
          {/* 连线预览：从起点手柄到当前鼠标的虚线，拖线全程可见 */}
          {connectFromNode && connectPos && (
            <line data-testid="topology-connect-preview"
              x1={connectFromNode.position.x + NODE_W} y1={connectFromNode.position.y + NODE_H / 2}
              x2={connectPos.x} y2={connectPos.y}
              className="stroke-primary/70" strokeWidth={1.5} strokeDasharray="5 4" pointerEvents="none" />
          )}
          {state.draft.nodes.map((node) => (
            <g key={node.repo} data-testid={`topology-node-${node.repo}`} data-node={node.repo} className="cursor-move" tabIndex={0} role="group"
              aria-label={`${node.repo} ${node.roles.join(", ")}`}
              onPointerDown={() => { dragRepo.current = node.repo; }}
              onPointerUp={() => {
                // 拖线式成边：按住起点手柄拖到目标节点上松手（自身松手 = 取消选中态重置）
                if (connectFrom && connectFrom !== node.repo) {
                  onState(addTopologyEdge(state, { from: connectFrom, to: node.repo, protocol: "grpc" }));
                }
                cancelConnect();
                dragRepo.current = null;
              }}>
              <title>{node.repo}</title>
              <rect x={node.position.x} y={node.position.y} width={NODE_W} height={NODE_H} rx={8}
                className="fill-secondary stroke-border hover:stroke-primary/60" />
              {/* 入口身份条：coral 左缘竖条（与 GroupLabel eyebrow 同语言——「入口」全站归 primary） */}
              {node.roles.includes("entrypoint") && (
                <rect x={node.position.x} y={node.position.y} width={3} height={NODE_H} rx={1.5} className="fill-primary" />
              )}
              <text x={node.position.x + 10} y={node.position.y + 20} className="fill-foreground font-mono text-[11px]">
                {node.repo.length > NAME_MAX ? `${node.repo.slice(0, NAME_MAX - 1)}…` : node.repo}
              </text>
              <text x={node.position.x + 10} y={node.position.y + 37} className="fill-muted-foreground text-[10px]">{node.roles.join(" · ") || "—"}</text>
              {/* 连接柄：环+点（可拉出连线的「手柄」形态，悬停原生 tooltip 补操作说明） */}
              <circle cx={node.position.x + NODE_W + 7} cy={node.position.y + NODE_H / 2} r={6.5}
                className={connectFrom === node.repo ? "fill-primary/20 stroke-primary" : "fill-none stroke-primary/45"}
                aria-label={`${t("scan.correlation.topology.connect")} ${node.repo}`} role="button" tabIndex={0}
                onPointerDown={(e) => {
                  e.stopPropagation();
                  if (connectFrom === node.repo) { cancelConnect(); return; }
                  setConnectFrom(node.repo);
                  setConnectPos({ x: node.position.x + NODE_W + 7, y: node.position.y + NODE_H / 2 });
                }}>
                <title>{t("scan.correlation.topology.connectHandle")}</title>
              </circle>
              <circle cx={node.position.x + NODE_W + 7} cy={node.position.y + NODE_H / 2} r={2.5} className="fill-primary pointer-events-none" />
            </g>
          ))}
          </g>
        </svg>
        {/* 缩放条（画布右下角，工具画布通用语言）：−/＋ 以画布中心步进；百分比即
            缩放读数、点击回 100%；fit 按节点包围盒适配。溢出当前视口时 fit 亮
            primary 圆点——「图已出界，一键找回全局」的导航信号，非装饰。 */}
        <div className="absolute bottom-2 right-2 flex items-center rounded-md border border-border bg-card/90 p-0.5 shadow-sm backdrop-blur-[2px]">
          <Button type="button" variant="ghost" size="icon-sm" aria-label={t("scan.correlation.topology.zoomOut")}
            title={t("scan.correlation.topology.zoomOut")}
            onClick={() => setView((v) => zoomAtPoint(v, { x: CANVAS_W / 2, y: CANVAS_H / 2 }, 1 / ZOOM_STEP))}>
            <Minus className="h-3.5 w-3.5" />
          </Button>
          <Button type="button" variant="ghost" size="icon-sm" className="w-10 font-mono text-[11px] tabular-nums"
            aria-label={t("scan.correlation.topology.zoomReset")}
            title={t("scan.correlation.topology.zoomReset")}
            onClick={() => setView({ x: 0, y: 0, k: 1 })}>
            {Math.round(view.k * 100)}%
          </Button>
          <Button type="button" variant="ghost" size="icon-sm" aria-label={t("scan.correlation.topology.zoomIn")}
            title={t("scan.correlation.topology.zoomIn")}
            onClick={() => setView((v) => zoomAtPoint(v, { x: CANVAS_W / 2, y: CANVAS_H / 2 }, ZOOM_STEP))}>
            <Plus className="h-3.5 w-3.5" />
          </Button>
          <div className="mx-0.5 h-4 w-px bg-border" aria-hidden />
          <Button type="button" variant="ghost" size="icon-sm" className="relative" aria-label={t("scan.correlation.topology.zoomFit")}
            title={t("scan.correlation.topology.zoomFit")}
            onClick={() => setView(fitTransform(state.draft.nodes))}>
            <Maximize className={`h-3.5 w-3.5 ${needsFit ? "text-primary" : ""}`} />
            {needsFit && <span data-testid="topology-fit-dot" className="absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-primary" aria-hidden />}
          </Button>
        </div>
        </div>
        {/* 右栏证据/覆盖详情：与画布等高内部滚动（原无上限，evidence 多时把编辑区撑长失衡） */}
        <aside className="space-y-3 rounded-lg border border-border bg-card p-3 xl:max-h-[420px] xl:overflow-y-auto" aria-label={t("scan.correlation.topology.details")}>
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
                <Select value={selectedEdge.protocol}
                  onValueChange={(v) => onState(updateTopologyEdge(state, selectedEdge.id, { protocol: v as never }))}>
                  <SelectTrigger className="h-8 w-full text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {["grpc", "http", "graphql"].map((p) => <SelectItem key={p} value={p}>{p}</SelectItem>)}
                  </SelectContent>
                </Select>
              </label>
              <label className="flex items-center gap-2">
                <Checkbox checked={selectedEdge.enabled}
                  onCheckedChange={(v) => onState(setTopologyEdgeEnabled(state, selectedEdge.id, v === true))} />
                {t("scan.correlation.topology.enabled")}
              </label>
              <Button type="button" variant="outline" size="sm" onClick={() => onState(deleteTopologyEdge(state, selectedEdge.id))}>
                {t("scan.correlation.topology.deleteEdge")}
              </Button>
              <div className="space-y-1 font-mono text-[11px] leading-relaxed text-muted-foreground">
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
