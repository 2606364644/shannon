# packages/whitebox/tests/test_run_report_polish.py
"""T5（spec 2026-08-26-report-generation-agent §5.4/§5.5）+ T4 接线。

覆盖：polish 重建 report_data（吃全部富化字段）/ ④摘要 LLM 产物与确定性
兜底 / ⑤QA 必填校验（taint 卡 endpoints≥1）与回炉一次 / §4.2（spec
2026-08-26-vuln-card-seven-sections）POC 写回时序前移——generate_poc_report
不再触发写回、write_structured_poc 独立 activity 写回 report_poc、
两处 worker 注册表含新 activity。
"""
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

from supernova_whitebox.pipeline import activities


class _FakeInput:
    def __init__(self, tmp_path):
        self.agent_name = None
        self.web_url = ""
        self.repo_path = str(tmp_path)
        self.deliverables_subdir = None
        self.workspace_name = None
        self.workspace_path = None
        self.config_path = None
        self.api_key = None
        self.pipeline_testing_mode = False
        self.prompt_override = None
        self.provider_config = None
        self.vuln_classes = None


class _RecordingSession:
    @asynccontextmanager
    async def track_step(self, phase, name, intent=None):
        yield


def _wb(tmp_path):
    d = tmp_path / "deliverables" / "whitebox"
    (d / "intermediate").mkdir(parents=True, exist_ok=True)
    return d


def _write_queue(d, vulns, name="xss_exploitation_queue.json"):
    d.joinpath("intermediate", name).write_text(
        json.dumps({"vulnerabilities": vulns}))


async def test_polish_rebuilds_with_enrichments_and_llm_summary(tmp_path, monkeypatch):
    """polish 重建 rd（含 report_endpoints/report_poc）+ ④LLM 摘要写回。"""
    d = _wb(tmp_path)
    _write_queue(d, [{
        "ID": "XSS-VULN-01", "vulnerability_type": "Stored",
        "externally_exploitable": True, "confidence": "high",
        "merge_source": "llm-only", "title": "t", "severity": "high",
        "report_endpoints": [{"method": "POST", "path": "/memos",
                              "route_registered_at": "app/routes/index.js:66",
                              "sink_location": "app/views/memos.html:31"}],
        "report_poc": {"witness_payload": "<img src=x>",
                       "request": {"method": "POST", "url": "http://t/memos"}},
    }])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    summary_payload = {
        "narrative": "本次扫描发现严重 XSS。",
        "risk_level": "极高",
        "top_risks": [{"vuln_id": "XSS-VULN-01", "reason": "可窃会话",
                       "priority": "P0"}],
        "remediation_order": "先修 XSS",
    }
    with patch.object(activities, "run_claude_prompt",
                      return_value=SimpleNamespace(
                          structured_output=summary_payload, text=None)):
        result = await activities.run_report_polish(_FakeInput(tmp_path))

    assert result["summary"] == "llm"
    data = json.loads(d.joinpath("report_data.json").read_text(encoding="utf-8"))
    assert data["executive_summary"]["narrative"] == "本次扫描发现严重 XSS。"
    assert data["executive_summary"]["top_risks"][0]["vuln_id"] == "XSS-VULN-01"
    v = data["vulnerabilities"][0]
    assert v["endpoints"][0]["route_registered_at"] == "app/routes/index.js:66"
    assert v["poc"]["request"]["method"] == "POST"
    assert data["qa"]["passed"] is True


async def test_polish_summary_deterministic_fallback(tmp_path, monkeypatch):
    """LLM 摘要失败 → 确定性摘要（top_risks 按 severity 排序）。"""
    d = _wb(tmp_path)
    _write_queue(d, [{
        "ID": "XSS-VULN-01", "vulnerability_type": "Stored",
        "externally_exploitable": True, "confidence": "high",
        "merge_source": "llm-only", "title": "t", "severity": "critical",
        "report_endpoints": [{"method": "POST", "path": "/memos"}],
    }])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))

    async def _boom(**kw):
        raise RuntimeError("llm down")
    with patch.object(activities, "run_claude_prompt", side_effect=_boom):
        result = await activities.run_report_polish(_FakeInput(tmp_path))

    assert result["summary"] == "deterministic"
    data = json.loads(d.joinpath("report_data.json").read_text(encoding="utf-8"))
    es = data["executive_summary"]
    assert es["narrative"]          # 确定性摘要非空
    assert es["top_risks"][0]["vuln_id"] == "XSS-VULN-01"


async def test_polish_qa_flags_and_reworks_missing_endpoints(tmp_path, monkeypatch):
    """⑤QA：taint 卡缺 endpoints → 回炉一次（富化 agent）→ 仍缺则 qa.passed=false。"""
    d = _wb(tmp_path)
    _write_queue(d, [{
        "ID": "XSS-GN-01", "vulnerability_type": "Reflected",
        "externally_exploitable": True, "confidence": "unadjudicated",
        "merge_source": "gitnexus-only", "title": "t", "severity": "high",
    }])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    rework_payload = {"vulnerabilities": [{
        "id": "XSS-GN-01",
        "endpoints": [{"method": "GET", "path": "/contributions",
                       "sink_location": "app/routes/contributions.js:21"}],
    }]}
    with patch.object(activities, "run_claude_prompt",
                      return_value=SimpleNamespace(
                          structured_output={"narrative": "s",
                                             "risk_level": "高",
                                             "top_risks": [],
                                             "remediation_order": None},
                          text=None)), \
         patch.object(activities, "run_gitnexus_verdict_agent",
                      return_value=SimpleNamespace(
                          structured_output=rework_payload, text=None)):
        result = await activities.run_report_polish(_FakeInput(tmp_path))

    assert result["reworked"] == 1
    data = json.loads(d.joinpath("report_data.json").read_text(encoding="utf-8"))
    assert data["qa"]["passed"] is True
    assert data["qa"]["reworked_ids"] == ["XSS-GN-01"]
    v = data["vulnerabilities"][0]
    assert v["endpoints"][0]["path"] == "/contributions"
    # queue 也被回炉写回（md 下次 assemble 同源）
    queue = json.loads(d.joinpath("intermediate", "xss_exploitation_queue.json")
                       .read_text(encoding="utf-8"))
    assert queue["vulnerabilities"][0]["report_endpoints"][0]["path"] == \
        "/contributions"


async def test_polish_qa_flags_when_rework_also_fails(tmp_path, monkeypatch):
    """回炉也失败 → qa.passed=false + failed_ids 显式呈现（不静默）。"""
    d = _wb(tmp_path)
    _write_queue(d, [{
        "ID": "SSRF-GN-01", "vulnerability_type": "SSRF",
        "externally_exploitable": True, "confidence": "low",
        "merge_source": "gitnexus-only", "title": "t", "severity": "high",
    }])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))

    async def _boom_agent(**kw):
        raise RuntimeError("llm down")
    with patch.object(activities, "run_claude_prompt", side_effect=_boom_agent), \
         patch.object(activities, "run_gitnexus_verdict_agent",
                      side_effect=_boom_agent):
        await activities.run_report_polish(_FakeInput(tmp_path))

    data = json.loads(d.joinpath("report_data.json").read_text(encoding="utf-8"))
    assert data["qa"]["passed"] is False
    check = next(c for c in data["qa"]["checks"]
                 if "endpoints" in c["check"])
    assert "SSRF-GN-01" in check["failed_ids"]


# ---------- §4.2（spec 2026-08-26-vuln-card-seven-sections）POC 写回时序前移 ----------

_POC_QUEUE_VULN = {
    "ID": "XSS-VULN-01", "vulnerability_type": "Stored",
    "externally_exploitable": True, "confidence": "high",
    "merge_source": "llm-only", "title": "t", "severity": "high",
    "endpoint": "POST /memos",
    "witness_payload": "<img src=x onerror=alert(1)>",
    "authentication_required": "true",
}


async def test_generate_poc_report_no_longer_writes_report_poc(tmp_path, monkeypatch):
    """§4.2：generate_poc_report 只保留 PoC md 生成，不再触发结构化写回
    （写回拆到 write_structured_poc，前移到 render_findings 之前）。"""
    d = _wb(tmp_path)
    _write_queue(d, [_POC_QUEUE_VULN])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    with patch("supernova_core.services.poc_generator.PoCGenerator.generate",
               return_value=None) as mock_gen, \
         patch.object(activities, "run_claude_prompt") as mock_llm:
        await activities.generate_poc_report(_FakeInput(tmp_path))

    mock_gen.assert_called_once()   # md 文档生成保留
    mock_llm.assert_not_called()    # 不再吃结构化 POC 的 expected_response LLM
    queue = json.loads(d.joinpath("intermediate", "xss_exploitation_queue.json")
                       .read_text(encoding="utf-8"))
    assert "report_poc" not in queue["vulnerabilities"][0]


async def test_write_structured_poc_activity_writes_report_poc(tmp_path, monkeypatch):
    """§4.2：write_structured_poc 独立 activity 可跑，写回 report_poc。"""
    d = _wb(tmp_path)
    _write_queue(d, [_POC_QUEUE_VULN])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    expected = {"indicator": "响应含未转义 payload", "success_criteria": "alert 触发"}
    with patch.object(activities, "run_claude_prompt",
                      return_value=SimpleNamespace(
                          structured_output=expected, text=None)):
        await activities.write_structured_poc(_FakeInput(tmp_path))

    queue = json.loads(d.joinpath("intermediate", "xss_exploitation_queue.json")
                       .read_text(encoding="utf-8"))
    poc = queue["vulnerabilities"][0]["report_poc"]
    assert poc["request"]["method"] == "POST"
    assert poc["request"]["url"].endswith("/memos")
    assert poc["expected_response"]["indicator"] == "响应含未转义 payload"
    assert "curl" in poc and poc["curl"]


def test_write_structured_poc_registered_on_workers():
    """b51eb9a4 教训：新 activity 两处 worker 注册表（CLI worker.py + web
    runner.py）都必须 import + 列入 activities，漏注册会 fail-fast/静默不跑。"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    for rel in ("packages/whitebox/src/supernova_whitebox/worker.py",
                "packages/worker/src/supernova_worker/runner.py"):
        src = (root / rel).read_text(encoding="utf-8")
        count = src.count("write_structured_poc")
        assert count >= 2, (
            f"write_structured_poc 在 {rel} 仅出现 {count} 次，"
            f"预期 >= 2（import + activities 列表）"
        )
