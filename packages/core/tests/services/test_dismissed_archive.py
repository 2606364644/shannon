"""dismissed_findings.json 留档（spec 2026-08-27 §4）——白盒两轨非漏洞判定留档。

口径（用户 2026-08-27 锁定）：
- 白盒判非漏洞（GN chain_verdict not_vulnerable / LLM 轨探索排除）→ 留档，
  不进报告（GN queue 不含、SSOT 天然干净）。
- 白盒拿不准（needs_review / unadjudicated）→ 保守进 queue / 报告。
- 黑盒验证失败进黑盒报告（带步骤+原因），不进本留档。
"""
import json

import pytest

from supernova_core.services.dismissed_archive import (
    append_dismissed,
    split_dismissed,
)


def _card(ID, verdict, *, confidence="high", title="t", mismatch_reason=None,
          evidence_chain="src->sink", source="q (ep)", sink_call="s:1",
          vuln_type="injection", model_cls=None):
    """最小 duck-typing 卡——分流只读 verdict/confidence/title/mismatch_reason/
    evidence_chain/source/sink_call/vulnerability_type 字段。"""
    if model_cls is not None:
        return model_cls(ID=ID, vulnerability_type=vuln_type, verdict=verdict,
                         confidence=confidence, title=title,
                         mismatch_reason=mismatch_reason,
                         evidence_chain=evidence_chain, source=source,
                         sink_call=sink_call)

    class _Card:
        pass

    c = _Card()
    c.ID = ID
    c.vulnerability_type = vuln_type
    c.verdict = verdict
    c.confidence = confidence
    c.title = title
    c.mismatch_reason = mismatch_reason
    c.evidence_chain = evidence_chain
    c.source = source
    c.sink_call = sink_call
    return c


# --- split_dismissed ------------------------------------------------------- #

def test_split_routes_not_vulnerable_to_dismissed():
    cards = [
        _card("INJ-GN-01", "vulnerable"),
        _card("INJ-GN-02", "safe",
              mismatch_reason="parameterized query, no concat",
              confidence="high", title="False lead: search q"),
        _card("INJ-GN-03", "needs_review"),
    ]
    queue, dismissed = split_dismissed(cards, vuln_class="injection")
    assert [c.ID for c in queue] == ["INJ-GN-01", "INJ-GN-03"]
    assert len(dismissed) == 1
    d = dismissed[0]
    assert d["ID"] == "INJ-GN-02"
    assert d["source_track"] == "gitnexus"
    assert d["vuln_class"] == "injection"
    assert d["dismissed_at_stage"] == "chain-verdict"
    assert d["dismiss_reason"] == "parameterized query, no concat"
    assert d["evidence"] == "src->sink"
    assert d["confidence"] == "high"
    assert d["title"] == "False lead: search q"
    assert d["source"] == "q (ep)"
    assert d["sink_call"] == "s:1"


def test_split_keeps_unadjudicated_and_none_verdict_in_queue():
    """没判成（unadjudicated）/ 无 verdict 的卡保守进 queue——「没判成≠非漏洞」。"""
    cards = [
        _card("XSS-GN-01", "needs_review", confidence="unadjudicated"),
        _card("SSRF-GN-01", None),
    ]
    queue, dismissed = split_dismissed(cards, vuln_class="xss")
    assert len(queue) == 2
    assert dismissed == []


def test_split_dismiss_reason_falls_back_to_verdict_word():
    """mismatch_reason 缺失时 dismiss_reason 不为空——回落 verdict 原词。"""
    _, dismissed = split_dismissed(
        [_card("INJ-GN-09", "safe")], vuln_class="injection")
    assert dismissed[0]["dismiss_reason"]


# --- append_dismissed ------------------------------------------------------ #

def _entry(ID, **kw):
    base = {"ID": ID, "source_track": "gitnexus", "vuln_class": "injection",
            "title": "t", "dismiss_reason": "r", "evidence": "e",
            "confidence": "high", "source": "s", "sink_call": "k",
            "dismissed_at_stage": "chain-verdict"}
    base.update(kw)
    return base


def test_append_dismissed_creates_file(tmp_path):
    p = tmp_path / "dismissed_findings.json"
    append_dismissed(p, [_entry("INJ-GN-02")])
    data = json.loads(p.read_text("utf-8"))
    assert [d["ID"] for d in data["dismissed"]] == ["INJ-GN-02"]


def test_append_dismissed_merges_and_same_id_overwrites(tmp_path):
    p = tmp_path / "dismissed_findings.json"
    append_dismissed(p, [_entry("INJ-GN-02"), _entry("LLM-01",
                                                      source_track="llm",
                                                      dismissed_at_stage="llm-exploration")])
    append_dismissed(p, [_entry("INJ-GN-02", dismiss_reason="updated reason"),
                          _entry("XSS-GN-05")])
    ids = [d["ID"] for d in json.loads(p.read_text("utf-8"))["dismissed"]]
    assert ids == ["INJ-GN-02", "LLM-01", "XSS-GN-05"]
    updated = [d for d in json.loads(p.read_text("utf-8"))["dismissed"]
               if d["ID"] == "INJ-GN-02"][0]
    assert updated["dismiss_reason"] == "updated reason"


def test_append_dismissed_empty_entries_no_write(tmp_path):
    p = tmp_path / "dismissed_findings.json"
    append_dismissed(p, [])
    assert not p.exists()


def test_append_dismissed_survives_corrupt_existing_file(tmp_path):
    """已有文件损坏 → 覆盖重写（留档不因坏文件全拒），但 best-effort 保留可读性。"""
    p = tmp_path / "dismissed_findings.json"
    p.write_text("{not json", encoding="utf-8")
    append_dismissed(p, [_entry("INJ-GN-02")])
    ids = [d["ID"] for d in json.loads(p.read_text("utf-8"))["dismissed"]]
    assert ids == ["INJ-GN-02"]
