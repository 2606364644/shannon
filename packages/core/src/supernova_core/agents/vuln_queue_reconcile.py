"""vuln queue roster 对账（spec 2026-08-19 §3.4）——纯函数，无 SDK / LLM 依赖。

collector 单条上交的丢失源是模型行为遗漏（研判了忘调 submit_finding，内容从未到达
host）；session 末尾的 finding_roster 声明（几百字符小 payload）是全量账本。本模块
把两份账确定性对齐，产出六分支判定（写盘 / 空写 / 不写 / 漏交清单 / 多交清单）。

分支表（spec §3.4）：
- roster N = submitted N（ID 一致）      → 完整写盘
- roster N > submitted（缺 ID）          → missing 非空（executor 定向重查后写盘）
- submitted 多出 roster                  → 保留（召回优先）+ extra_ids（warning）
- roster=[] 且 submitted=[]              → write_empty_queue（真·无漏洞）
- roster 缺失（summary 没调）且 submitted=[] → skip_write（防线整跑重试）
- roster 缺失且 submitted 非空           → 写盘 + 跳过对账（已收数据不丢）
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ReconcileResult:
    merged: list[dict] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)   # [{id, title}] 待定向重查
    extra_ids: list[str] = field(default_factory=list)  # submitted 有 roster 无（保留）
    overwritten_ids: list[str] = field(default_factory=list)  # 同 ID 重复上交（后交覆盖）
    roster_present: bool = False
    write_empty_queue: bool = False
    skip_write: bool = False


def dedupe_by_id(items: list[dict]) -> tuple[list[dict], list[str]]:
    """submit_finding 累积按 ID 去重、后交覆盖（模型修正场景）；返回 (去重列表, 被覆盖 ID)。"""
    by_id: dict[str, dict] = {}
    overwritten: list[str] = []
    for it in items or []:
        fid = str(it.get("ID", ""))
        if not fid:
            continue
        if fid in by_id:
            overwritten.append(fid)
        by_id[fid] = it
    return list(by_id.values()), overwritten


def backfill_titles_from_roster(items: list[dict],
                                roster: list[dict] | None) -> list[str]:
    """用 roster 的 {id, title} 回填 items 里缺空 title 的条目（原地改）。

    2026-09-03 NodeGoat 首扫回归：GLM submit_finding 可交合法 JSON 但漏
    title（工具层 required 校验管新数据，旧 queue / 漏网数据由本层兜），
    而 finding_roster（set_findings_summary 全量账本）里 title 齐全。
    仅回填缺/None/空白 title；已有实质值不覆盖；roster 条目 title 空或
    id 不在 roster 的条目不动。返回回填成功的 ID 列表（可观测日志用）。
    """
    by_id = {
        str(r.get("id", "")): str(r.get("title", "") or "").strip()
        for r in (roster or [])
    }
    filled: list[str] = []
    for it in items or []:
        cur = it.get("title")
        if isinstance(cur, str) and cur.strip():
            continue
        fid = str(it.get("ID", ""))
        roster_title = by_id.get(fid, "")
        if roster_title:
            it["title"] = roster_title
            filled.append(fid)
    return filled


def reconcile_findings(
    submitted_items: list[dict] | None, roster: list[dict] | None
) -> ReconcileResult:
    res = ReconcileResult()
    res.merged, res.overwritten_ids = dedupe_by_id(submitted_items or [])
    res.roster_present = roster is not None

    if not res.roster_present:
        # 分支：roster 缺失——submitted 非空则写盘跳过对账；空则交给防线
        res.skip_write = not res.merged
        return res

    merged_ids = {str(f.get("ID", "")) for f in res.merged}
    roster_ids = {str(r.get("id", "")) for r in roster}
    res.missing = [
        {"id": str(r.get("id", "")), "title": str(r.get("title", ""))}
        for r in roster if str(r.get("id", "")) not in merged_ids
    ]
    res.extra_ids = sorted(merged_ids - roster_ids)
    res.write_empty_queue = (not roster) and (not res.merged)
    return res
