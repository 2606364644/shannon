"""合并层确定性校验与拼装（spec 2026-08-27 §6）——零推断。

- validate_vuln_refs：vuln_id 幻觉防护——不在对应 service queue 的 ID 集 → 标
  invalid_ref（透明单列，不删）。
- assemble_multi_hop_chains：边邻接启发——首边有攻击链（flows）且下游邻接边有
  calls 即拼链，basis/confidence 显式标注为 edge-adjacency / structural，不做
  函数级可达声明（留 spec §14）。
- sanitize_adjudication_cards：裁决卡 direction 与 conclusion 矛盾 → 拦下标
  needs-review（不丢弃，供人工复核）。
"""
from __future__ import annotations

import copy

# direction → 该方向下自洽的 conclusion 集合；不在表内的 direction 不校验
_CONSISTENT_CONCLUSIONS = {
    "upgrade": {"vulnerable"},
    "confirm": {"vulnerable"},
    "downgrade": {"downgraded", "not-vulnerable"},
    "maintain": {"not-vulnerable"},
}


def validate_vuln_refs(edges: list[dict],
                       per_service_id_sets: dict[str, set[str]]) -> list[dict]:
    """对 merged edges 中 flows 的 vuln_refs 标注幻觉引用（返回深拷贝）。

    只有「service 在 ID 集映射里 且 vuln_id 非空 且 不在集合」才标 invalid_ref——
    agent-discovered（无 vuln_id）与未知 service（无 queue）不属幻觉引用。
    """
    out = copy.deepcopy(edges)
    for e in out:
        for f in e.get("flows", []):
            for ref in f.get("vuln_refs", []):
                vid = ref.get("vuln_id")
                id_set = per_service_id_sets.get(ref.get("service"))
                if vid and id_set is not None and vid not in id_set:
                    ref["invalid_ref"] = True
    return out


def assemble_multi_hop_chains(edges: list[dict]) -> list[dict]:
    """边邻接启发拼多跳链：攻击链到达 X（某边 flows 非空）+ X 作为 from 的
    下游边有 calls → 候选链延伸。防环（路径节点不重复）；结果有界。"""
    by_from: dict[str, list[dict]] = {}
    for e in edges:
        by_from.setdefault(e["from"], []).append(e)

    chains: list[dict] = []

    def extend(path: list[str]) -> None:
        for e in by_from.get(path[-1], []):
            if not e.get("calls"):
                continue
            if e["to"] in path:
                continue    # 防环
            new_path = path + [e["to"]]
            chains.append({"path": new_path, "basis": "edge-adjacency",
                           "confidence": "structural"})
            extend(new_path)

    for start in edges:
        if start.get("flows"):
            extend([start["from"], start["to"]])
    return chains


def sanitize_adjudication_cards(cards: list[dict]) -> list[dict]:
    """direction/conclusion 矛盾 → conclusion 改 needs-review（其余不动）。"""
    for c in cards:
        allowed = _CONSISTENT_CONCLUSIONS.get(c.get("direction"))
        if allowed and c.get("conclusion") not in allowed:
            c["conclusion"] = "needs-review"
    return cards
