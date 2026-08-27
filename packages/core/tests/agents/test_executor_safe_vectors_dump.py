"""P3: executor 落 queue 时同步落 {vc}_safe_vectors.json。"""
import json
from pathlib import Path
from unittest.mock import MagicMock


def test_safe_vectors_dumped_alongside_queue(tmp_path: Path):
    from supernova_core.agents import executor

    deliverables = tmp_path
    (deliverables / "intermediate").mkdir()
    # collector payload bag with safe_vectors
    collector = MagicMock()
    collector.get_all.return_value = {
        "submitted_findings": [{"ID": "V1", "vulnerability_type": "injection",
                                "externally_exploitable": True, "confidence": "high"}],
        "findings_summary": {"finding_roster": [{"id": "V1", "title": "t"}]},
        "safe_vectors": {"vectors": [
            {"subject": "req.query.id", "location": "a.js:10", "defense_mechanism": "parseInt"},
        ]},
    }

    # 调被测落盘纯函数（见 Step 3）
    from supernova_core.agents.executor import _dump_safe_vectors
    _dump_safe_vectors(deliverables, "injection", collector.get_all())

    sv_path = deliverables / "intermediate" / "injection_safe_vectors.json"
    assert sv_path.exists()
    data = json.loads(sv_path.read_text(encoding="utf-8"))
    assert data["vectors"][0]["defense_mechanism"] == "parseInt"


def test_safe_vectors_skipped_when_empty(tmp_path: Path):
    """safe_vectors 缺失/空 → 不落盘（不产空文件）。"""
    from supernova_core.agents.executor import _dump_safe_vectors
    _dump_safe_vectors(tmp_path, "ssrf", {"safe_vectors": {"vectors": []}})
    assert not (tmp_path / "intermediate" / "ssrf_safe_vectors.json").exists()


# ===== spec 2026-08-27 §6：LLM 轨判非漏洞 → dismissed_findings.json 留档 =====

def test_archive_dismissed_from_safe_vectors(tmp_path):
    """safe_vectors（分析后确认健壮防护的向量=LLM 轨判非漏洞）→ 转写
    dismissed_findings.json（source_track=llm / dismissed_at_stage=
    llm-exploration / dismiss_reason=defense_mechanism）。复用现有
    set_safe_vectors collector 通道（spec §6 原计划新造 submit_dismissed，
    实现时发现语义已被 §4 safe_vectors 覆盖——零 prompt/collector 改动）。"""
    from supernova_core.agents.executor import (
        _archive_dismissed_from_safe_vectors,
    )

    bag = {"safe_vectors": {"vectors": [
        {"subject": "search 参数 q", "location": "controllers/shop.js:88",
         "defense_mechanism": "Prepared Statement (Parameter Binding)"},
        {"subject": "redirect_url", "location": "handlers/r.js:12",
         "defense_mechanism": "Strict URL Whitelist Validation"},
    ]}}
    _archive_dismissed_from_safe_vectors(tmp_path, "injection", bag)

    data = json.loads((tmp_path / "intermediate" / "dismissed_findings.json")
                      .read_text(encoding="utf-8"))
    entries = data["dismissed"]
    assert len(entries) == 2
    e = entries[0]
    assert e["source_track"] == "llm"
    assert e["vuln_class"] == "injection"
    assert e["dismissed_at_stage"] == "llm-exploration"
    assert e["title"] == "search 参数 q"
    assert e["dismiss_reason"] == "Prepared Statement (Parameter Binding)"
    assert e["evidence"] == "controllers/shop.js:88"


def test_archive_dismissed_no_safe_vectors_no_write(tmp_path):
    from supernova_core.agents.executor import (
        _archive_dismissed_from_safe_vectors,
    )

    _archive_dismissed_from_safe_vectors(tmp_path, "xss", {})
    assert not (tmp_path / "intermediate" / "dismissed_findings.json").exists()
