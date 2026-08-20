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
