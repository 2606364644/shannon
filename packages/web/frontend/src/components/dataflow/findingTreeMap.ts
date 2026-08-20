import type { DataflowView } from "@/api/types";

/**
 * finding_id → tree_id 映射（spec 2026-08-20 §5 路由与入口）。
 *
 * DeliverablesTab 用 SWR 拉同一 dataflow API（与 DataFlowTab 同 key → 共享缓存，
 * 零额外请求）后建映射传 VulnCard，展开态「查看数据流」链接 → ../dataflow?tree={tree_id}。
 * taint 树上的 finding 才有映射（auth/authz 不在树上、LLM 枝 finding 无 id → 无链接）。
 */
export function buildFindingTreeMap(view: DataflowView | null | undefined): Map<string, string> {
  const m = new Map<string, string>();
  if (!view) return m;
  for (const tree of view.trees) {
    for (const f of tree.findings) {
      if (f.id) m.set(f.id, tree.tree_id);
    }
  }
  return m;
}
