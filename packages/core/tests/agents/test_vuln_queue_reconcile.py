"""roster 对账纯函数（spec 2026-08-19 §3.4 六分支表）。

submitted = collector 的 submit_finding 累积（get_all()['submitted_findings']）；
roster = set_findings_summary.finding_roster（None=没调/没给字段）。
"""
from supernova_core.agents.vuln_queue_reconcile import (
    ReconcileResult,
    dedupe_by_id,
    reconcile_findings,
)


def _sub(i: int) -> dict:
    return {"ID": f"AUTH-VULN-{i:02d}", "title": f"t{i}"}


def _roster(i: int) -> dict:
    return {"id": f"AUTH-VULN-{i:02d}", "title": f"t{i}"}


def test_dedupe_by_id_later_wins():
    items = [_sub(1), _sub(2), {**_sub(1), "title": "revised"}]
    merged, dup = dedupe_by_id(items)
    assert [f["ID"] for f in merged] == ["AUTH-VULN-01", "AUTH-VULN-02"]
    assert merged[0]["title"] == "revised"  # 后交覆盖
    assert dup == ["AUTH-VULN-01"]


def test_full_match_passes():
    rec = reconcile_findings([_sub(i) for i in (1, 2, 3)],
                             [_roster(i) for i in (1, 2, 3)])
    assert isinstance(rec, ReconcileResult)
    assert len(rec.merged) == 3 and not rec.missing and not rec.extra_ids
    assert rec.roster_present and not rec.write_empty_queue and not rec.skip_write


def test_missing_detected():
    rec = reconcile_findings([_sub(1), _sub(2)],
                             [_roster(1), _roster(2), _roster(7)])
    assert rec.missing == [_roster(7)]
    assert not rec.skip_write


def test_extra_kept_recall_first():
    rec = reconcile_findings([_sub(1), _sub(9)],
                             [_roster(1)])
    assert rec.extra_ids == ["AUTH-VULN-09"]
    assert any(f["ID"] == "AUTH-VULN-09" for f in rec.merged)  # 保留


def test_true_zero_vulns_writes_empty_queue():
    rec = reconcile_findings([], [])
    assert rec.roster_present and rec.write_empty_queue and not rec.skip_write
    assert rec.merged == [] and rec.missing == []


def test_total_defiance_skips_write():
    """无 roster 无提交 → skip_write=True（不写盘，validator 防线整跑重试）。"""
    rec = reconcile_findings(None, None)
    assert rec.skip_write and not rec.write_empty_queue
    assert rec.merged == [] and not rec.missing


def test_no_roster_but_submitted_writes_and_skips_reconcile():
    rec = reconcile_findings([_sub(1), _sub(2)], None)
    assert not rec.roster_present and not rec.skip_write and not rec.write_empty_queue
    assert len(rec.merged) == 2 and rec.missing == []  # 跳过对账，已收数据不丢


def test_none_submitted_with_roster_means_all_missing():
    rec = reconcile_findings(None, [_roster(1), _roster(2)])
    assert rec.missing == [_roster(1), _roster(2)] and not rec.skip_write


# ---------- roster title 回填（2026-09-03 NodeGoat 空标题回归） ----------
# GLM submit_finding 7 条中 6 条漏 title（合法 JSON 缺字段，工具层静默收录），
# 但 finding_roster 里 7 条 {id,title} 全有——roster 是全量账本，写盘前用它
# 回填缺空 title，零 LLM 成本修复空标题。

from supernova_core.agents.vuln_queue_reconcile import backfill_titles_from_roster


def test_backfill_fills_missing_title_key():
    items = [{"ID": "A", "title": "kept"}, {"ID": "B"}]
    roster = [{"id": "A", "title": "roster-a"}, {"id": "B", "title": "roster-b"}]
    filled = backfill_titles_from_roster(items, roster)
    assert filled == ["B"]
    assert items[0]["title"] == "kept"        # 已有不覆盖
    assert items[1]["title"] == "roster-b"    # 缺 → roster 回填


def test_backfill_fills_blank_and_null_titles():
    items = [{"ID": "A", "title": None}, {"ID": "B", "title": "  "}]
    roster = [{"id": "A", "title": "ra"}, {"id": "B", "title": "rb"}]
    filled = backfill_titles_from_roster(items, roster)
    assert filled == ["A", "B"]
    assert items[0]["title"] == "ra" and items[1]["title"] == "rb"


def test_backfill_skips_when_roster_title_blank_or_absent():
    items = [{"ID": "A"}, {"ID": "B"}]
    roster = [{"id": "A", "title": ""}, {"id": "A", "title": "  "}]
    filled = backfill_titles_from_roster(items, roster)
    assert filled == []
    assert "title" not in items[0]            # roster 没有可用 title，不动


def test_backfill_no_roster_is_noop():
    items = [{"ID": "A"}]
    assert backfill_titles_from_roster(items, None) == []
    assert items == [{"ID": "A"}]
