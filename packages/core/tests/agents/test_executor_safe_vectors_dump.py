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
