// 目录侧栏（spec 2026-08-20 §5「页面骨架：左目录 + 右内容两栏」）。
// 分组镜像页面三区：漏洞数据流树 (N) / 认证·授权风险 (N) / 排查过的入口 (N)。
// 每棵树一条：状态图标（●红=有打通枝 / ✂绿=全部剪断）+ sink 名 + 次行小字
// （finding IDs · N打通/M剪断）；认证·授权条目 ▲黄 + endpoint。
// IntersectionObserver scrollspy：滚动到哪棵树对应条目高亮；点击平滑滚动 +
// 目标卡 coral 描边闪烁（focusDataflowAnchor 与 DataFlowTab ?tree= 深链共用）。
// 吸顶 / 自身内滚 / 窄屏 <1000px 顶部块由 DataFlowTab 两栏布局承载（sticky 列）。
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ControlFinding, DataflowTree, SafeVector } from "@/api/types";
import { controlAnchorId } from "./GuardChain";

export interface TocSideBarProps {
  trees: DataflowTree[];
  controls: ControlFinding[];
  safeVectors: SafeVector[];
}

/** 排查过的入口分组锚点 id（对应 SafeEntries 区的 data-safe-section）。 */
export const SAFE_SECTION_ID = "safe-entries";

/** 描边闪烁自动清理延时（≈ tokens.css .dataflow-flash 动画时长 1.8s + 余量）。 */
const FLASH_CLEANUP_MS = 2000;
/** 闪烁清理定时器（单例：新定位自动接管，旧定时器不再对新目标生效）。 */
let flashTimer: number | undefined;

function cssEscape(s: string): string {
  return typeof CSS !== "undefined" && CSS.escape ? CSS.escape(s) : s.replace(/["\\]/g, "\\$&");
}

/** 定位锚点：平滑滚动到目标卡（树卡 / 关卡卡 / 排查过的入口区）+ coral 描边闪烁。
 *  目录点击与 DataFlowTab ?tree= 深链（VulnCard「查看数据流」跳转落点）共用。
 *  找不到目标（深链失效 / 数据未含该树）返回 false，静默不报错。
 *  单一 active-target 语义（Fix round 1 F①）：触发新定位前清掉所有旧目标的描边
 *  （连点多目标不残留累积），闪烁结束后自动摘除（描边是瞬时定位提示，非持久状态）。 */
export function focusDataflowAnchor(id: string): boolean {
  const selector =
    id === SAFE_SECTION_ID
      ? "[data-safe-section]"
      : `[data-tree-id="${cssEscape(id)}"], [data-control-id="${cssEscape(id)}"]`;
  const el = document.querySelector(selector);
  if (!el) return false;
  // 清旧：摘掉所有仍带描边的目标 + 作废旧清理定时器
  for (const prev of Array.from(document.querySelectorAll(".dataflow-flash"))) {
    prev.classList.remove("dataflow-flash");
  }
  if (flashTimer !== undefined) {
    window.clearTimeout(flashTimer);
    flashTimer = undefined;
  }
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  // 重启动画：先摘 class 再强制 reflow 再加回（连续定位同一目标也能重新闪烁）
  el.classList.remove("dataflow-flash");
  void (el as HTMLElement).offsetWidth;
  el.classList.add("dataflow-flash");
  // 动画结束自清理（jsdom 无动画事件，用与动画时长对齐的 timer 兜底）
  flashTimer = window.setTimeout(() => {
    el.classList.remove("dataflow-flash");
    flashTimer = undefined;
  }, FLASH_CLEANUP_MS);
  return true;
}

/** 树是否有漏洞（打通口径，与 PruningTreeFig 靶心一致）：任一枝 verdict=vulnerable 或挂 findings。
 *  目录状态图标与 DataFlowTab「只看有漏洞的」筛选（Task 14）共用同一判定，避免口径漂移。 */
export function treeHasVuln(tree: DataflowTree): boolean {
  return tree.branches.some((b) => b.verdict === "vulnerable") || tree.findings.length > 0;
}

/** 树状态：●红=有打通枝（或挂 findings）/ ✂绿=全部剪断（与 PruningTreeFig 靶心口径一致）。 */
function treeStatus(tree: DataflowTree): "vuln" | "safe" {
  return treeHasVuln(tree) ? "vuln" : "safe";
}

/** 锚点元素 → TOC id（树卡 data-tree-id / 关卡卡 data-control-id / safe 区固定 id）。 */
function anchorIdOf(el: Element): string | null {
  const tid = el.getAttribute("data-tree-id");
  if (tid) return tid;
  const cid = el.getAttribute("data-control-id");
  if (cid) return cid;
  return el.hasAttribute("data-safe-section") ? SAFE_SECTION_ID : null;
}

export function TocSideBar({ trees, controls, safeVectors }: TocSideBarProps) {
  const { t } = useTranslation();
  const [activeId, setActiveId] = useState<string | null>(null);
  const visibleRef = useRef<Set<string>>(new Set());

  // scrollspy：观察右内容区锚点，按文档序取第一个仍可见者高亮（多区同屏时顶部优先）。
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const els = Array.from(
      document.querySelectorAll("[data-tree-id], [data-control-id], [data-safe-section]"),
    );
    if (els.length === 0) return;
    const order = els.map(anchorIdOf).filter((x): x is string => x !== null);
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const id = anchorIdOf(e.target);
          if (!id) continue;
          if (e.isIntersecting) visibleRef.current.add(id);
          else visibleRef.current.delete(id);
        }
        const first = order.find((id) => visibleRef.current.has(id));
        if (first) setActiveId(first);
      },
      // 视口上 10%~40% 带内命中才算「当前区块」（读者视线区）
      { rootMargin: "-10% 0px -60% 0px" },
    );
    for (const el of els) io.observe(el);
    return () => io.disconnect();
  }, [trees, controls, safeVectors]);

  const locate = (id: string) => {
    setActiveId(id);
    focusDataflowAnchor(id);
  };

  const entryCls = (id: string) =>
    `flex w-full items-start gap-1.5 rounded-sm p-1.5 text-left text-sm ${
      activeId === id ? "bg-accent text-accent-foreground" : "hover:bg-accent/60"
    }`;

  return (
    <nav aria-label={t("workspaceDetail.dataflow.tocAria")} data-testid="dataflow-toc" className="space-y-4 text-sm">
      {trees.length > 0 && (
        <div className="space-y-1">
          <p className="px-1.5 text-xs font-medium text-muted-foreground">
            {t("workspaceDetail.dataflow.tocGroupTrees", { count: trees.length })}
          </p>
          {trees.map((tree) => {
            const status = treeStatus(tree);
            const vulnN = tree.branches.filter((b) => b.verdict === "vulnerable").length;
            const safeN = tree.branches.filter((b) => b.verdict === "safe").length;
            const ids = tree.findings.map((f) => f.id).filter(Boolean).join(", ");
            const counts = t("workspaceDetail.dataflow.tocCounts", { vuln: vulnN, safe: safeN });
            return (
              <button
                key={tree.tree_id}
                type="button"
                data-toc-id={tree.tree_id}
                data-status={status}
                aria-current={activeId === tree.tree_id ? "true" : undefined}
                onClick={() => locate(tree.tree_id)}
                className={entryCls(tree.tree_id)}
              >
                <span
                  aria-hidden
                  className="mt-0.5 shrink-0 font-bold"
                  style={{ color: status === "vuln" ? "hsl(var(--c-red))" : "hsl(var(--c-green))" }}
                >
                  {status === "vuln" ? "●" : "✂"}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">
                    {tree.sink.label ?? t("workspaceDetail.dataflow.sink")}
                  </span>
                  <span className="block truncate text-[11px] text-muted-foreground">
                    {ids ? `${ids} · ` : ""}
                    {counts}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      )}

      {controls.length > 0 && (
        <div className="space-y-1">
          <p className="px-1.5 text-xs font-medium text-muted-foreground">
            {t("workspaceDetail.dataflow.tocGroupControls", { count: controls.length })}
          </p>
          {controls.map((c, i) => {
            const id = controlAnchorId(c, i);
            return (
              <button
                key={id}
                type="button"
                data-toc-id={id}
                data-status="control"
                aria-current={activeId === id ? "true" : undefined}
                onClick={() => locate(id)}
                className={entryCls(id)}
              >
                <span
                  aria-hidden
                  className="mt-0.5 shrink-0 font-bold"
                  style={{ color: "hsl(var(--c-yellow))" }}
                >
                  ▲
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-mono text-[13px]">
                    {c.endpoint ?? c.id ?? id}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      )}

      {safeVectors.length > 0 && (
        <div className="space-y-1">
          {/* 排查过的入口无逐条锚点（平铺一区）→ 分组头即定位入口 */}
          <button
            type="button"
            data-toc-id={SAFE_SECTION_ID}
            data-status="safe"
            aria-current={activeId === SAFE_SECTION_ID ? "true" : undefined}
            onClick={() => locate(SAFE_SECTION_ID)}
            className={`${entryCls(SAFE_SECTION_ID)} px-1.5 py-1 text-xs font-medium text-muted-foreground`}
          >
            <span aria-hidden className="shrink-0" style={{ color: "hsl(var(--c-green))" }}>
              ✔
            </span>
            {t("workspaceDetail.dataflow.tocGroupSafe", { count: safeVectors.length })}
          </button>
        </div>
      )}
    </nav>
  );
}
